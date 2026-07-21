"""Owner-scoped alert rules and durable alert-event creation.

The evaluator deliberately uses the latest normalized snapshot and a bounded historical
minimum. It never treats a missing price as zero and it does not send network requests;
delivery is handled by the notification worker after the transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlertEvent,
    AlertRule,
    AlertRuleChannel,
    CollectionRun,
    Itinerary,
    NotificationChannel,
    NotificationDelivery,
    PriceObservation,
    SearchQuery,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.models.enums import CollectionStatus, DeliveryStatus
from app.services.fare_data import FareFilterSpec, itinerary_filter_conditions


class AlertError(Exception):
    pass


class AlertNotFoundError(AlertError):
    pass


class AlertConflictError(AlertError):
    pass


class AlertConfigurationError(AlertError):
    pass


@dataclass(frozen=True, slots=True)
class AlertRuleView:
    rule: AlertRule
    channel_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AlertEventView:
    event: AlertEvent
    subscription_id: UUID


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    run_id: UUID
    evaluated_rules: int
    created_events: int
    created_deliveries: int


async def list_alert_rules(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID | None = None,
    limit: int = 100,
) -> list[AlertRuleView]:
    statement = (
        select(AlertRule)
        .join(Subscription, Subscription.id == AlertRule.subscription_id)
        .where(AlertRule.user_id == user_id, Subscription.user_id == user_id)
        .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
        .limit(min(limit, 200))
    )
    if subscription_id is not None:
        statement = statement.where(AlertRule.subscription_id == subscription_id)
    rules = (await session.scalars(statement)).all()
    if not rules:
        return []
    channel_rows = (
        await session.execute(
            select(AlertRuleChannel.alert_rule_id, AlertRuleChannel.notification_channel_id).where(
                AlertRuleChannel.alert_rule_id.in_([rule.id for rule in rules])
            )
        )
    ).all()
    channels_by_rule: dict[UUID, list[UUID]] = {}
    for rule_id, channel_id in channel_rows:
        channels_by_rule.setdefault(rule_id, []).append(channel_id)
    return [
        AlertRuleView(rule=rule, channel_ids=tuple(channels_by_rule.get(rule.id, ())))
        for rule in rules
    ]


async def create_alert_rule(
    session: AsyncSession,
    *,
    user: User,
    subscription_id: UUID,
    name: str,
    rule_type: str,
    enabled: bool,
    threshold_price_minor: int | None,
    threshold_currency: str | None,
    threshold_percentage: int | None,
    comparison_window_days: int | None,
    cooldown_seconds: int,
    channel_ids: list[UUID],
    rule_config: dict[str, object],
) -> AlertRuleView:
    subscription = await session.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user.id,
        )
    )
    if subscription is None:
        raise AlertNotFoundError("subscription not found")
    query = await session.scalar(
        select(SearchQuery).where(SearchQuery.id == subscription.search_query_id)
    )
    if query is None:
        raise AlertNotFoundError("search query not found")
    await _validate_rule_values(
        session,
        user_id=user.id,
        subscription=subscription,
        rule_type=rule_type,
        threshold_price_minor=threshold_price_minor,
        threshold_currency=threshold_currency,
        threshold_percentage=threshold_percentage,
        comparison_window_days=comparison_window_days,
        channel_ids=channel_ids,
    )
    rule = AlertRule(
        id=uuid4(),
        user_id=user.id,
        subscription_id=subscription.id,
        name=name.strip(),
        rule_type=rule_type,
        enabled=enabled,
        threshold_price_minor=threshold_price_minor,
        threshold_currency=(threshold_currency or query.currency).upper()
        if threshold_currency or threshold_price_minor is not None
        else None,
        threshold_percentage=threshold_percentage,
        comparison_window_days=comparison_window_days,
        cooldown_seconds=cooldown_seconds,
        rule_config=dict(rule_config),
    )
    session.add(rule)
    await session.flush()
    await _replace_rule_channels(session, rule.id, user.id, channel_ids)
    return AlertRuleView(rule=rule, channel_ids=tuple(channel_ids))


async def update_alert_rule(
    session: AsyncSession,
    *,
    user: User,
    rule_id: UUID,
    updates: dict[str, Any],
) -> AlertRuleView:
    rule = await _load_rule(session, user_id=user.id, rule_id=rule_id, lock=True)
    subscription = await session.scalar(
        select(Subscription).where(Subscription.id == rule.subscription_id)
    )
    if subscription is None:
        raise AlertNotFoundError("subscription not found")
    channel_ids = updates.pop("channel_ids", None)
    await _validate_rule_values(
        session,
        user_id=user.id,
        subscription=subscription,
        rule_type=rule.rule_type,
        threshold_price_minor=updates.get("threshold_price_minor", rule.threshold_price_minor),
        threshold_currency=updates.get("threshold_currency", rule.threshold_currency),
        threshold_percentage=updates.get("threshold_percentage", rule.threshold_percentage),
        comparison_window_days=updates.get(
            "comparison_window_days", rule.comparison_window_days
        ),
        channel_ids=channel_ids,
    )
    for key, value in updates.items():
        if value is not None and hasattr(rule, key):
            setattr(rule, key, value.upper() if key == "threshold_currency" else value)
    if channel_ids is not None:
        await _replace_rule_channels(session, rule.id, user.id, channel_ids)
    await session.flush()
    return await _rule_view(session, rule)


async def delete_alert_rule(session: AsyncSession, *, user_id: UUID, rule_id: UUID) -> None:
    rule = await _load_rule(session, user_id=user_id, rule_id=rule_id, lock=True)
    await session.delete(rule)


async def list_alert_events(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
    as_of: datetime | None = None,
    before_created_at: datetime | None = None,
    before_id: UUID | None = None,
) -> tuple[list[AlertEventView], bool]:
    statement = (
        select(AlertEvent, AlertRule.subscription_id)
        .join(AlertRule, AlertRule.id == AlertEvent.alert_rule_id)
        .where(AlertEvent.user_id == user_id)
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
        .limit(min(limit, 100) + 1)
    )
    if as_of is not None:
        statement = statement.where(AlertEvent.created_at <= as_of)
    if before_created_at is not None and before_id is not None:
        statement = statement.where(
            tuple_(AlertEvent.created_at, AlertEvent.id) < (before_created_at, before_id)
        )
    rows = (await session.execute(statement)).all()
    has_more = len(rows) > min(limit, 100)
    rows = rows[: min(limit, 100)]
    return [AlertEventView(event=row[0], subscription_id=row[1]) for row in rows], has_more


async def list_notification_deliveries(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 100,
) -> list[NotificationDelivery]:
    statement = (
        select(NotificationDelivery)
        .join(AlertEvent, AlertEvent.id == NotificationDelivery.alert_event_id)
        .where(AlertEvent.user_id == user_id)
        .order_by(NotificationDelivery.updated_at.desc(), NotificationDelivery.id.desc())
        .limit(min(limit, 200))
    )
    return list((await session.scalars(statement)).all())


async def evaluate_collection_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    now: datetime | None = None,
) -> EvaluationResult:
    """Evaluate all owner rules for one successful run under one DB transaction."""

    now = now or datetime.now(UTC)
    run = await session.scalar(
        select(CollectionRun).where(CollectionRun.id == run_id).with_for_update()
    )
    if run is None:
        raise AlertNotFoundError("collection run not found")
    if run.status != CollectionStatus.SUCCEEDED.value:
        return EvaluationResult(
            run_id=run_id, evaluated_rules=0, created_events=0, created_deliveries=0
        )
    if getattr(run, "alerts_evaluated_at", None) is not None:
        return EvaluationResult(
            run_id=run_id, evaluated_rules=0, created_events=0, created_deliveries=0
        )

    query = await session.scalar(select(SearchQuery).where(SearchQuery.id == run.search_query_id))
    if query is None:
        raise AlertNotFoundError("search query not found")
    rule_rows = (
        await session.execute(
            select(AlertRule, Subscription, SubscriptionFilter)
            .join(Subscription, Subscription.id == AlertRule.subscription_id)
            .join(
                SubscriptionFilter,
                SubscriptionFilter.subscription_id == Subscription.id,
            )
            .where(
                Subscription.search_query_id == query.id,
                Subscription.enabled.is_(True),
                AlertRule.enabled.is_(True),
            )
            .order_by(AlertRule.id)
        )
    ).all()
    created_events = 0
    created_deliveries = 0
    for rule, _subscription, subscription_filter in rule_rows:
        filters = FareFilterSpec.from_subscription(query, subscription_filter)
        if rule.rule_type == "direct_available":
            filters = replace(filters, direct_only=True)
        current = await _current_price(
            session,
            run_id=run.id,
            query_id=query.id,
            currency=(rule.threshold_currency or query.currency).upper(),
            filters=filters,
        )
        if current is None:
            continue
        previous_min = await _previous_minimum(
            session,
            current_run_id=run.id,
            query_id=query.id,
            currency=current.currency,
            observed_at=run.finished_at or now,
            days=rule.comparison_window_days,
            filters=filters,
        )
        triggered, event_type, title, body = _evaluate_rule(
            rule,
            query=query,
            current_price=current.total_price_minor,
            current_currency=current.currency,
            previous_minimum=previous_min,
            direct=current.is_direct,
        )
        if not triggered:
            continue
        recent = await session.scalar(
            select(AlertEvent.id)
            .where(
                AlertEvent.alert_rule_id == rule.id,
                AlertEvent.created_at >= now - timedelta(seconds=rule.cooldown_seconds),
            )
            .limit(1)
        )
        if recent is not None:
            continue
        deduplication_key = f"{rule.id}:{run.id}:{event_type}"[:180]
        event_insert = (
            pg_insert(AlertEvent)
            .values(
                id=uuid4(),
                user_id=rule.user_id,
                alert_rule_id=rule.id,
                collection_run_id=run.id,
                deduplication_key=deduplication_key,
                event_type=event_type,
                severity=rule.severity,
                title=title,
                body=body,
                event_payload={
                    "priceMinor": current.total_price_minor,
                    "currency": current.currency,
                    "previousMinimumMinor": previous_min,
                    "isDirect": current.is_direct,
                    "searchQueryId": str(query.id),
                },
            )
            .on_conflict_do_nothing(index_elements=[AlertEvent.deduplication_key])
            .returning(AlertEvent.id)
        )
        event_id = (await session.execute(event_insert)).scalar_one_or_none()
        if event_id is None:
            continue
        created_events += 1
        channels = await _channels_for_rule(session, rule)
        if not channels:
            continue
        delivery_rows = [
            {
                "id": uuid4(),
                "alert_event_id": event_id,
                "notification_channel_id": channel.id,
                "status": DeliveryStatus.PENDING.value,
                "attempt_count": 0,
                "next_attempt_at": now,
                "response_metadata": {},
            }
            for channel in channels
        ]
        delivery_insert = pg_insert(NotificationDelivery).values(delivery_rows)
        delivery_insert = delivery_insert.on_conflict_do_nothing(
            index_elements=[
                NotificationDelivery.alert_event_id,
                NotificationDelivery.notification_channel_id,
            ]
        )
        result = await session.execute(delivery_insert)
        created_deliveries += max(result.rowcount or 0, 0)

    if hasattr(run, "alerts_evaluated_at"):
        run.alerts_evaluated_at = now
    else:
        run.run_metadata = {**(run.run_metadata or {}), "alerts_evaluated_at": now.isoformat()}
    await session.flush()
    return EvaluationResult(
        run_id=run_id,
        evaluated_rules=len(rule_rows),
        created_events=created_events,
        created_deliveries=created_deliveries,
    )


@dataclass(frozen=True, slots=True)
class _CurrentPrice:
    total_price_minor: int
    currency: str
    is_direct: bool


async def _current_price(
    session: AsyncSession,
    *,
    run_id: UUID,
    query_id: UUID,
    currency: str,
    filters: FareFilterSpec,
) -> _CurrentPrice | None:
    statement = (
        select(
            PriceObservation.total_price_minor,
            PriceObservation.currency,
            PriceObservation.is_direct,
        )
        .where(
            PriceObservation.collection_run_id == run_id,
            PriceObservation.search_query_id == query_id,
            PriceObservation.currency == currency,
        )
        .order_by(PriceObservation.total_price_minor, PriceObservation.id)
        .limit(1)
    )
    if filters.direct_only:
        statement = statement.where(PriceObservation.is_direct.is_(True))
    if filters.max_price_minor is not None:
        statement = statement.where(
            PriceObservation.total_price_minor <= filters.max_price_minor
        )
    if filters.requires_itinerary_scan:
        statement = statement.join(
            Itinerary,
            Itinerary.id == PriceObservation.itinerary_id,
        ).where(
            Itinerary.search_query_id == query_id,
            *itinerary_filter_conditions(filters),
        )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return _CurrentPrice(
        total_price_minor=row.total_price_minor,
        currency=row.currency,
        is_direct=row.is_direct,
    )


async def _previous_minimum(
    session: AsyncSession,
    *,
    current_run_id: UUID,
    query_id: UUID,
    currency: str,
    observed_at: datetime,
    days: int | None,
    filters: FareFilterSpec,
) -> int | None:
    statement = select(func.min(PriceObservation.total_price_minor)).where(
        PriceObservation.search_query_id == query_id,
        PriceObservation.collection_run_id != current_run_id,
        PriceObservation.currency == currency,
        PriceObservation.observed_at < observed_at,
    )
    if filters.direct_only:
        statement = statement.where(PriceObservation.is_direct.is_(True))
    if filters.max_price_minor is not None:
        statement = statement.where(
            PriceObservation.total_price_minor <= filters.max_price_minor
        )
    if filters.requires_itinerary_scan:
        statement = statement.join(
            Itinerary,
            Itinerary.id == PriceObservation.itinerary_id,
        ).where(
            Itinerary.search_query_id == query_id,
            *itinerary_filter_conditions(filters),
        )
    else:
        statement = statement.where(PriceObservation.is_lowest.is_(True))
    if days is not None:
        statement = statement.where(
            PriceObservation.observed_at >= observed_at - timedelta(days=days)
        )
    return await session.scalar(statement)


def _evaluate_rule(
    rule: AlertRule,
    *,
    query: SearchQuery,
    current_price: int,
    current_currency: str,
    previous_minimum: int | None,
    direct: bool,
) -> tuple[bool, str, str, str]:
    route = str((query.normalized_query or {}).get("label") or query.query_hash[:12])
    if rule.rule_type == "price_threshold":
        triggered = (
            rule.threshold_price_minor is not None
            and current_currency == (rule.threshold_currency or query.currency).upper()
            and current_price <= rule.threshold_price_minor
        )
        return (
            triggered,
            "price_threshold",
            f"{route} 达到目标价",
            f"当前最低价 {current_price / 100:.2f} {current_currency}，不高于设定目标。",
        )
    if rule.rule_type == "new_low":
        triggered = previous_minimum is None or current_price < previous_minimum
        return (
            triggered,
            "new_low",
            f"{route} 出现新低",
            f"当前最低价 {current_price / 100:.2f} {current_currency}。",
        )
    if rule.rule_type == "percentage_drop":
        drop = (
            0
            if previous_minimum in (None, 0)
            else int((previous_minimum - current_price) * 10000 / previous_minimum)
        )
        triggered = (
            previous_minimum is not None
            and current_price < previous_minimum
            and rule.threshold_percentage is not None
            and drop >= rule.threshold_percentage
        )
        return (
            triggered,
            "percentage_drop",
            f"{route} 价格下降",
            f"当前最低价较历史基准下降 {drop / 100:.2f}%。",
        )
    if rule.rule_type == "absolute_drop":
        drop = 0 if previous_minimum is None else previous_minimum - current_price
        triggered = (
            previous_minimum is not None
            and rule.threshold_price_minor is not None
            and drop >= rule.threshold_price_minor
        )
        return (
            triggered,
            "absolute_drop",
            f"{route} 价格下降",
            f"当前价格比历史基准低 {drop / 100:.2f} {current_currency}。",
        )
    if rule.rule_type == "direct_available":
        triggered = direct and (
            rule.threshold_price_minor is None or current_price <= rule.threshold_price_minor
        )
        return (
            triggered,
            "direct_available",
            f"{route} 有直飞报价",
            f"直飞最低价 {current_price / 100:.2f} {current_currency}。",
        )
    if rule.rule_type == "round_trip_range":
        lower = _config_int(rule.rule_config, "minPriceMinor")
        upper = _config_int(rule.rule_config, "maxPriceMinor")
        triggered = (lower is None or current_price >= lower) and (
            upper is None or current_price <= upper
        )
        return (
            triggered,
            "round_trip_range",
            f"{route} 进入价格区间",
            f"往返价格 {current_price / 100:.2f} {current_currency} 在设定区间内。",
        )
    return False, rule.rule_type, "", ""


async def _load_rule(
    session: AsyncSession,
    *,
    user_id: UUID,
    rule_id: UUID,
    lock: bool = False,
) -> AlertRule:
    statement = select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    rule = await session.scalar(statement)
    if rule is None:
        raise AlertNotFoundError("alert rule not found")
    return rule


async def _rule_view(session: AsyncSession, rule: AlertRule) -> AlertRuleView:
    channel_ids = (
        await session.scalars(
            select(AlertRuleChannel.notification_channel_id).where(
                AlertRuleChannel.alert_rule_id == rule.id
            )
        )
    ).all()
    return AlertRuleView(rule=rule, channel_ids=tuple(channel_ids))


async def _replace_rule_channels(
    session: AsyncSession,
    rule_id: UUID,
    user_id: UUID,
    channel_ids: list[UUID],
) -> None:
    if channel_ids:
        owned = set(
            (
                await session.scalars(
                    select(NotificationChannel.id).where(
                        NotificationChannel.user_id == user_id,
                        NotificationChannel.id.in_(channel_ids),
                    )
                )
            ).all()
        )
        if owned != set(channel_ids):
            raise AlertConfigurationError("all notification channels must belong to the user")
    await session.execute(
        delete(AlertRuleChannel).where(AlertRuleChannel.alert_rule_id == rule_id)
    )
    if channel_ids:
        session.add_all(
            [
                AlertRuleChannel(alert_rule_id=rule_id, notification_channel_id=channel_id)
                for channel_id in dict.fromkeys(channel_ids)
            ]
        )


async def _channels_for_rule(
    session: AsyncSession,
    rule: AlertRule,
) -> list[NotificationChannel]:
    explicit = (
        await session.scalars(
            select(NotificationChannel)
            .join(
                AlertRuleChannel,
                AlertRuleChannel.notification_channel_id == NotificationChannel.id,
            )
            .where(
                AlertRuleChannel.alert_rule_id == rule.id,
                NotificationChannel.user_id == rule.user_id,
                NotificationChannel.enabled.is_(True),
            )
        )
    ).all()
    if explicit:
        return list(explicit)
    return list(
        (
            await session.scalars(
                select(NotificationChannel).where(
                    NotificationChannel.user_id == rule.user_id,
                    NotificationChannel.enabled.is_(True),
                )
            )
        ).all()
    )


async def _validate_rule_values(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription: Subscription,
    rule_type: str,
    threshold_price_minor: int | None,
    threshold_currency: str | None,
    threshold_percentage: int | None,
    comparison_window_days: int | None,
    channel_ids: list[UUID] | None,
) -> None:
    if (
        rule_type in {"price_threshold", "absolute_drop", "direct_available"}
        and threshold_price_minor is None
    ):
        raise AlertConfigurationError("this rule requires thresholdPriceMinor")
    if rule_type == "percentage_drop" and threshold_percentage is None:
        raise AlertConfigurationError("percentage_drop requires thresholdPercentage")
    if threshold_currency is not None and len(threshold_currency.strip()) != 3:
        raise AlertConfigurationError("threshold currency must be a three-letter code")
    if channel_ids is not None and len(set(channel_ids)) != len(channel_ids):
        raise AlertConfigurationError("notification channels must be unique")
    # The subscription ownership check is intentionally repeated at the service boundary.
    if subscription.user_id != user_id:
        raise AlertNotFoundError("subscription not found")


def _config_int(config: dict[str, object], key: str) -> int | None:
    value = config.get(key)
    return value if isinstance(value, int) and value >= 0 else None

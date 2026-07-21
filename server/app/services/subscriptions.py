from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.search import FareSearch
from app.models import (
    AlertRule,
    AuditEvent,
    SearchLeg,
    SearchQuery,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.repositories.canonical_searches import get_or_create_canonical_search


class SubscriptionNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    subscription: Subscription
    search_query: SearchQuery
    subscription_filter: SubscriptionFilter
    legs: Sequence[SearchLeg]
    target_price_minor: int | None
    target_currency: str | None


@dataclass(frozen=True, slots=True)
class SubscriptionPage:
    items: list[SubscriptionView]
    has_more: bool


async def create_subscription(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    search: FareSearch,
    target_price_minor: int | None,
    poll_interval_seconds: int,
    enabled: bool,
    tags: list[str],
) -> SubscriptionView:
    canonical_search, _ = await get_or_create_canonical_search(session, search)
    now = datetime.now(UTC)
    subscription = Subscription(
        user_id=user.id,
        search_query_id=canonical_search.id,
        name=name.strip(),
        enabled=enabled,
        poll_interval_seconds=poll_interval_seconds,
        next_due_at=now if enabled else None,
        tags=_normalize_tags(tags),
    )
    session.add(subscription)
    await session.flush()

    local_filters = search.local_filter_payload()
    subscription_filter = SubscriptionFilter(
        subscription_id=subscription.id,
        airline_codes=local_filters["airline_codes"],
        origin_airport_codes=local_filters["departure_airports"],
        destination_airport_codes=local_filters["arrival_airports"],
        max_price_minor=local_filters["max_price_minor"],
        currency=search.currency if local_filters["max_price_minor"] is not None else None,
        max_stops=local_filters["max_stops"],
        max_duration_minutes=local_filters["max_duration_minutes"],
        departure_time_start_minutes=local_filters["departure_minute_start"],
        departure_time_end_minutes=local_filters["departure_minute_end"],
    )
    session.add(subscription_filter)
    if target_price_minor is not None:
        session.add(
            AlertRule(
                user_id=user.id,
                subscription_id=subscription.id,
                name=f"{subscription.name} 目标价",
                rule_type="price_threshold",
                enabled=enabled,
                threshold_price_minor=target_price_minor,
                threshold_currency=search.currency,
                cooldown_seconds=21_600,
                rule_config={"source": "subscription_target_price"},
            )
        )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="subscription.created",
            target_type="subscription",
            target_id=str(subscription.id),
            metadata_json={"query_hash": canonical_search.query_hash},
            summary=f"Subscription created: {subscription.name}",
        )
    )
    await session.flush()
    return await _load_subscription_view(
        session,
        user_id=user.id,
        subscription_id=subscription.id,
    )


async def list_subscription_views(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int,
    as_of: datetime,
    before_created_at: datetime | None = None,
    before_id: UUID | None = None,
) -> SubscriptionPage:
    statement = (
        select(Subscription, SearchQuery, SubscriptionFilter)
        .join(SearchQuery, SearchQuery.id == Subscription.search_query_id)
        .join(
            SubscriptionFilter,
            SubscriptionFilter.subscription_id == Subscription.id,
        )
        .where(
            Subscription.user_id == user_id,
            Subscription.created_at <= as_of,
        )
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .limit(limit + 1)
    )
    if before_created_at is not None and before_id is not None:
        statement = statement.where(
            tuple_(Subscription.created_at, Subscription.id) < (before_created_at, before_id)
        )
    rows = (
        await session.execute(statement)
    ).all()
    if not rows:
        return SubscriptionPage(items=[], has_more=False)

    has_more = len(rows) > limit
    rows = rows[:limit]

    query_ids = {search_query.id for _, search_query, _ in rows}
    leg_rows = (
        await session.scalars(
            select(SearchLeg)
            .where(SearchLeg.search_query_id.in_(query_ids))
            .order_by(SearchLeg.search_query_id, SearchLeg.position)
        )
    ).all()
    legs_by_query: dict[UUID, list[SearchLeg]] = {}
    for leg in leg_rows:
        legs_by_query.setdefault(leg.search_query_id, []).append(leg)
    target_rules = await _load_target_rules(
        session,
        subscription_ids={subscription.id for subscription, _, _ in rows},
    )

    return SubscriptionPage(
        items=[
            SubscriptionView(
                subscription=subscription,
                search_query=search_query,
                subscription_filter=subscription_filter,
                legs=legs_by_query.get(search_query.id, ()),
                target_price_minor=target_rules.get(subscription.id, (None, None))[0],
                target_currency=target_rules.get(subscription.id, (None, None))[1],
            )
            for subscription, search_query, subscription_filter in rows
        ],
        has_more=has_more,
    )


async def get_subscription_view(
    session: AsyncSession, *, user_id: UUID, subscription_id: UUID
) -> SubscriptionView:
    return await _load_subscription_view(
        session,
        user_id=user_id,
        subscription_id=subscription_id,
    )


async def set_subscription_enabled(
    session: AsyncSession,
    *,
    user: User,
    subscription_id: UUID,
    enabled: bool,
) -> SubscriptionView:
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user.id)
        .with_for_update()
    )
    if subscription is None:
        raise SubscriptionNotFoundError

    subscription.enabled = enabled
    subscription.next_due_at = datetime.now(UTC) if enabled else None
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="subscription.enabled" if enabled else "subscription.paused",
            target_type="subscription",
            target_id=str(subscription.id),
            summary="Subscription enabled" if enabled else "Subscription paused",
        )
    )
    await session.flush()
    return await _load_subscription_view(
        session,
        user_id=user.id,
        subscription_id=subscription.id,
    )


async def delete_subscription(
    session: AsyncSession, *, user: User, subscription_id: UUID
) -> None:
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user.id)
        .with_for_update()
    )
    if subscription is None:
        raise SubscriptionNotFoundError

    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="subscription.deleted",
            target_type="subscription",
            target_id=str(subscription.id),
            metadata_json={"name": subscription.name},
            summary="Subscription deleted",
        )
    )
    await session.execute(delete(Subscription).where(Subscription.id == subscription.id))


async def _load_subscription_view(
    session: AsyncSession, *, user_id: UUID, subscription_id: UUID
) -> SubscriptionView:
    row = (
        await session.execute(
            select(Subscription, SearchQuery, SubscriptionFilter)
            .join(SearchQuery, SearchQuery.id == Subscription.search_query_id)
            .join(
                SubscriptionFilter,
                SubscriptionFilter.subscription_id == Subscription.id,
            )
            .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise SubscriptionNotFoundError
    subscription, search_query, subscription_filter = row
    legs = (
        await session.scalars(
            select(SearchLeg)
            .where(SearchLeg.search_query_id == search_query.id)
            .order_by(SearchLeg.position)
        )
    ).all()
    target_rules = await _load_target_rules(session, subscription_ids={subscription.id})
    target_price_minor, target_currency = target_rules.get(
        subscription.id,
        (None, None),
    )
    return SubscriptionView(
        subscription=subscription,
        search_query=search_query,
        subscription_filter=subscription_filter,
        legs=legs,
        target_price_minor=target_price_minor,
        target_currency=target_currency,
    )


async def _load_target_rules(
    session: AsyncSession,
    *,
    subscription_ids: set[UUID],
) -> dict[UUID, tuple[int | None, str | None]]:
    if not subscription_ids:
        return {}
    rules = (
        await session.scalars(
            select(AlertRule)
            .where(
                AlertRule.subscription_id.in_(subscription_ids),
                AlertRule.rule_type == "price_threshold",
                AlertRule.threshold_price_minor.is_not(None),
            )
            .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
        )
    ).all()
    targets: dict[UUID, tuple[int | None, str | None]] = {}
    for rule in rules:
        if (rule.rule_config or {}).get("source") != "subscription_target_price":
            continue
        targets.setdefault(
            rule.subscription_id,
            (rule.threshold_price_minor, rule.threshold_currency),
        )
    return targets


def next_collection_time(*, poll_interval_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=poll_interval_seconds)


def _normalize_tags(tags: list[str]) -> list[str]:
    return sorted({tag.strip()[:40] for tag in tags if tag.strip()})

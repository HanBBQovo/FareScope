from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditEvent,
    ComparisonView,
    ComparisonViewItem,
    SearchQuery,
    Subscription,
    User,
)

MAX_COMPARISON_VIEWS_PER_USER = 20
VALID_TREND_DAYS = frozenset((7, 30, 90))


class ComparisonError(ValueError):
    pass


class ComparisonNotFoundError(ComparisonError):
    pass


class ComparisonConflictError(ComparisonError):
    pass


class ComparisonLimitError(ComparisonError):
    pass


class ComparisonVersionConflictError(ComparisonConflictError):
    pass


@dataclass(frozen=True, slots=True)
class ComparisonViewRecord:
    view: ComparisonView
    subscription_ids: tuple[UUID, ...]

    @property
    def active_route_count(self) -> int:
        return len(self.subscription_ids)

    @property
    def missing_subscription_count(self) -> int:
        return max(0, self.view.configured_route_count - self.active_route_count)

    @property
    def comparable(self) -> bool:
        return self.active_route_count >= 2


@dataclass(frozen=True, slots=True)
class ComparisonViewPage:
    items: tuple[ComparisonViewRecord, ...]
    has_more: bool


def normalize_comparison_name(name: str) -> tuple[str, str]:
    display_name = " ".join(name.split())
    if not display_name:
        raise ComparisonError("comparison name is required")
    if len(display_name) > 160:
        raise ComparisonError("comparison name cannot exceed 160 characters")
    return display_name, display_name.casefold()


def comparison_request_fingerprint(
    *,
    name: str,
    subscription_ids: tuple[UUID, ...],
    trend_days: int,
) -> str:
    display_name, _ = normalize_comparison_name(name)
    payload = json.dumps(
        {
            "name": display_name,
            "subscription_ids": [str(value) for value in subscription_ids],
            "trend_days": trend_days,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def create_comparison_view(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    subscription_ids: tuple[UUID, ...],
    trend_days: int,
    idempotency_key: str,
    max_views: int = MAX_COMPARISON_VIEWS_PER_USER,
) -> tuple[ComparisonViewRecord, bool]:
    _validate_route_request(subscription_ids, trend_days=trend_days)
    display_name, normalized_name = normalize_comparison_name(name)
    fingerprint = comparison_request_fingerprint(
        name=display_name,
        subscription_ids=subscription_ids,
        trend_days=trend_days,
    )
    await _acquire_owner_lock(session, user_id=user.id)

    existing = await session.scalar(
        select(ComparisonView).where(
            ComparisonView.user_id == user.id,
            ComparisonView.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise ComparisonConflictError("idempotency key was already used for another comparison")
        return await _load_comparison_record(session, view=existing), False

    existing_name = await session.scalar(
        select(ComparisonView.id).where(
            ComparisonView.user_id == user.id,
            ComparisonView.normalized_name == normalized_name,
        )
    )
    if existing_name is not None:
        raise ComparisonConflictError("a comparison with this name already exists")

    view_count = await session.scalar(
        select(func.count()).select_from(ComparisonView).where(ComparisonView.user_id == user.id)
    )
    if int(view_count or 0) >= max_views:
        raise ComparisonLimitError("the saved comparison limit has been reached")

    currency = await _validate_owned_routes(
        session,
        user_id=user.id,
        subscription_ids=subscription_ids,
    )
    view = ComparisonView(
        user_id=user.id,
        name=display_name,
        normalized_name=normalized_name,
        currency=currency,
        trend_days=trend_days,
        version=1,
        configured_route_count=len(subscription_ids),
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(view)
    await session.flush()
    session.add_all(
        ComparisonViewItem(
            comparison_view_id=view.id,
            subscription_id=subscription_id,
            user_id=user.id,
            position=position,
        )
        for position, subscription_id in enumerate(subscription_ids)
    )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="comparison.created",
            target_type="comparison_view",
            target_id=str(view.id),
            metadata_json={"route_count": len(subscription_ids), "currency": currency},
            summary=f"Comparison created: {display_name}",
        )
    )
    await session.flush()
    return ComparisonViewRecord(view=view, subscription_ids=subscription_ids), True


async def list_comparison_views(
    session: AsyncSession,
    *,
    user_id: UUID,
    as_of: datetime,
    limit: int,
    before_created_at: datetime | None = None,
    before_id: UUID | None = None,
) -> ComparisonViewPage:
    statement = select(ComparisonView).where(
        ComparisonView.user_id == user_id,
        ComparisonView.created_at <= as_of,
    )
    if before_created_at is not None and before_id is not None:
        statement = statement.where(
            tuple_(ComparisonView.created_at, ComparisonView.id) < (before_created_at, before_id)
        )
    views = (
        await session.scalars(
            statement.order_by(ComparisonView.created_at.desc(), ComparisonView.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    has_more = len(views) > limit
    views = views[:limit]
    if not views:
        return ComparisonViewPage(items=(), has_more=False)

    items = (
        await session.scalars(
            select(ComparisonViewItem)
            .where(ComparisonViewItem.comparison_view_id.in_({view.id for view in views}))
            .order_by(ComparisonViewItem.comparison_view_id, ComparisonViewItem.position)
        )
    ).all()
    ids_by_view: dict[UUID, list[UUID]] = {}
    for item in items:
        ids_by_view.setdefault(item.comparison_view_id, []).append(item.subscription_id)
    return ComparisonViewPage(
        items=tuple(
            ComparisonViewRecord(
                view=view,
                subscription_ids=tuple(ids_by_view.get(view.id, ())),
            )
            for view in views
        ),
        has_more=has_more,
    )


async def get_comparison_view(
    session: AsyncSession,
    *,
    user_id: UUID,
    comparison_id: UUID,
) -> ComparisonViewRecord:
    rows = (
        await session.execute(
            select(ComparisonView, ComparisonViewItem.subscription_id)
            .outerjoin(
                ComparisonViewItem,
                ComparisonViewItem.comparison_view_id == ComparisonView.id,
            )
            .where(
                ComparisonView.id == comparison_id,
                ComparisonView.user_id == user_id,
            )
            .order_by(ComparisonViewItem.position)
        )
    ).all()
    if not rows:
        raise ComparisonNotFoundError("comparison not found")
    return ComparisonViewRecord(
        view=rows[0][0],
        subscription_ids=tuple(row.subscription_id for row in rows if row.subscription_id),
    )


async def replace_comparison_view(
    session: AsyncSession,
    *,
    user: User,
    comparison_id: UUID,
    name: str,
    subscription_ids: tuple[UUID, ...],
    trend_days: int,
    expected_version: int,
) -> ComparisonViewRecord:
    _validate_route_request(subscription_ids, trend_days=trend_days)
    display_name, normalized_name = normalize_comparison_name(name)
    await _acquire_owner_lock(session, user_id=user.id)
    view = await session.scalar(
        select(ComparisonView)
        .where(
            ComparisonView.id == comparison_id,
            ComparisonView.user_id == user.id,
        )
        .with_for_update()
    )
    if view is None:
        raise ComparisonNotFoundError("comparison not found")
    current = await _load_comparison_record(session, view=view)
    same_configuration = (
        view.name == display_name
        and view.trend_days == trend_days
        and current.subscription_ids == subscription_ids
    )
    if view.version != expected_version:
        if same_configuration:
            return current
        raise ComparisonVersionConflictError("comparison was changed by another request")
    if same_configuration:
        return current

    existing_name = await session.scalar(
        select(ComparisonView.id).where(
            ComparisonView.user_id == user.id,
            ComparisonView.normalized_name == normalized_name,
            ComparisonView.id != view.id,
        )
    )
    if existing_name is not None:
        raise ComparisonConflictError("a comparison with this name already exists")
    currency = await _validate_owned_routes(
        session,
        user_id=user.id,
        subscription_ids=subscription_ids,
    )

    await session.execute(
        delete(ComparisonViewItem).where(ComparisonViewItem.comparison_view_id == view.id)
    )
    session.add_all(
        ComparisonViewItem(
            comparison_view_id=view.id,
            subscription_id=subscription_id,
            user_id=user.id,
            position=position,
        )
        for position, subscription_id in enumerate(subscription_ids)
    )
    view.name = display_name
    view.normalized_name = normalized_name
    view.currency = currency
    view.trend_days = trend_days
    view.configured_route_count = len(subscription_ids)
    view.version += 1
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="comparison.updated",
            target_type="comparison_view",
            target_id=str(view.id),
            metadata_json={
                "route_count": len(subscription_ids),
                "currency": currency,
                "version": view.version,
            },
            summary="Comparison updated",
        )
    )
    await session.flush()
    return ComparisonViewRecord(view=view, subscription_ids=subscription_ids)


async def delete_comparison_view(
    session: AsyncSession,
    *,
    user: User,
    comparison_id: UUID,
) -> None:
    view = await session.scalar(
        select(ComparisonView)
        .where(
            ComparisonView.id == comparison_id,
            ComparisonView.user_id == user.id,
        )
        .with_for_update()
    )
    if view is None:
        raise ComparisonNotFoundError("comparison not found")
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="comparison.deleted",
            target_type="comparison_view",
            target_id=str(view.id),
            metadata_json={"name": view.name},
            summary="Comparison deleted",
        )
    )
    await session.execute(delete(ComparisonView).where(ComparisonView.id == view.id))


async def _load_comparison_record(
    session: AsyncSession,
    *,
    view: ComparisonView,
) -> ComparisonViewRecord:
    subscription_ids = tuple(
        (
            await session.scalars(
                select(ComparisonViewItem.subscription_id)
                .where(ComparisonViewItem.comparison_view_id == view.id)
                .order_by(ComparisonViewItem.position)
            )
        ).all()
    )
    return ComparisonViewRecord(view=view, subscription_ids=subscription_ids)


async def _validate_owned_routes(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_ids: tuple[UUID, ...],
) -> str:
    rows = (
        await session.execute(
            select(Subscription.id, SearchQuery.currency)
            .join(SearchQuery, SearchQuery.id == Subscription.search_query_id)
            .where(
                Subscription.user_id == user_id,
                Subscription.id.in_(subscription_ids),
            )
        )
    ).all()
    if len(rows) != len(subscription_ids) or {row.id for row in rows} != set(subscription_ids):
        raise ComparisonNotFoundError("one or more comparison routes were not found")
    currencies = {row.currency for row in rows}
    if len(currencies) != 1:
        raise ComparisonError("comparison routes must use the same currency")
    return currencies.pop()


async def _acquire_owner_lock(session: AsyncSession, *, user_id: UUID) -> None:
    lock_name = f"farescope:comparison-quota:{user_id}"
    await session.scalar(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_name, 0))))


def _validate_route_request(subscription_ids: tuple[UUID, ...], *, trend_days: int) -> None:
    if not 2 <= len(subscription_ids) <= 8:
        raise ComparisonError("comparison requires between 2 and 8 routes")
    if len(set(subscription_ids)) != len(subscription_ids):
        raise ComparisonError("comparison routes must be unique")
    if trend_days not in VALID_TREND_DAYS:
        raise ComparisonError("comparison trend must be 7, 30, or 90 days")

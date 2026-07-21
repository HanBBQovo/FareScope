from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectionRun, Provider, SearchQuery, Subscription
from app.models.enums import CollectionStatus
from app.services.collection_dispatch import DispatchLease, lease_collection_runs


@dataclass(frozen=True, slots=True)
class SchedulerPlan:
    due_subscription_count: int
    grouped_query_count: int
    created_run_count: int
    recovered_run_count: int
    exhausted_run_count: int
    dispatch_leases: tuple[DispatchLease, ...]
    maintained_partition_count: int


async def plan_scheduler_tick(
    session: AsyncSession,
    *,
    now: datetime,
    subscription_batch_size: int,
    dispatch_batch_size: int,
    dispatch_lease_seconds: int,
    schedule_bucket_seconds: int,
) -> SchedulerPlan:
    """Plan database work only; callers publish leases after this transaction commits."""

    if subscription_batch_size < 1 or dispatch_batch_size < 1:
        raise ValueError("scheduler batch sizes must be positive")
    if schedule_bucket_seconds < 1:
        raise ValueError("schedule bucket must be positive")

    recovered_count, exhausted_count = await _recover_expired_leases(
        session,
        now=now,
        limit=dispatch_batch_size,
    )

    due_subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(
                Subscription.enabled.is_(True),
                Subscription.next_due_at.is_not(None),
                Subscription.next_due_at <= now,
            )
            .order_by(Subscription.next_due_at, Subscription.id)
            .with_for_update(skip_locked=True)
            .limit(subscription_batch_size)
        )
    ).all()
    grouped: dict[UUID, list[Subscription]] = defaultdict(list)
    for subscription in due_subscriptions:
        grouped[subscription.search_query_id].append(subscription)

    created_count = await _ensure_grouped_collection_runs(
        session,
        grouped=grouped,
        now=now,
        schedule_bucket_seconds=schedule_bucket_seconds,
    )
    for subscription in due_subscriptions:
        subscription.next_due_at = advance_due_at(
            subscription.next_due_at,
            poll_interval_seconds=subscription.poll_interval_seconds,
            now=now,
        )

    dispatch_leases = await lease_collection_runs(
        session,
        now=now,
        limit=dispatch_batch_size,
        lease_seconds=dispatch_lease_seconds,
    )
    await session.flush()
    return SchedulerPlan(
        due_subscription_count=len(due_subscriptions),
        grouped_query_count=len(grouped),
        created_run_count=created_count,
        recovered_run_count=recovered_count,
        exhausted_run_count=exhausted_count,
        dispatch_leases=dispatch_leases,
        maintained_partition_count=0,
    )


def advance_due_at(
    previous_due_at: datetime | None,
    *,
    poll_interval_seconds: int,
    now: datetime,
) -> datetime:
    """Advance from the previous schedule without accumulating scheduler drift."""

    if poll_interval_seconds < 1:
        raise ValueError("poll interval must be positive")
    if previous_due_at is None or previous_due_at > now:
        return now + timedelta(seconds=poll_interval_seconds)
    elapsed_seconds = (now - previous_due_at).total_seconds()
    periods = int(elapsed_seconds // poll_interval_seconds) + 1
    return previous_due_at + timedelta(seconds=periods * poll_interval_seconds)


def scheduled_idempotency_key(
    *,
    search_query_id: UUID,
    now: datetime,
    bucket_seconds: int,
) -> str:
    if bucket_seconds < 1:
        raise ValueError("schedule bucket must be positive")
    bucket = int(now.timestamp() // bucket_seconds)
    return f"scheduled:{search_query_id}:{bucket}"


async def _ensure_grouped_collection_runs(
    session: AsyncSession,
    *,
    grouped: dict[UUID, list[Subscription]],
    now: datetime,
    schedule_bucket_seconds: int,
) -> int:
    if not grouped:
        return 0

    query_ids = tuple(grouped)
    queries = (
        await session.scalars(select(SearchQuery).where(SearchQuery.id.in_(query_ids)))
    ).all()
    query_by_id = {query.id: query for query in queries}
    provider_codes = {query.provider for query in queries}
    providers = (
        await session.scalars(
            select(Provider).where(
                Provider.code.in_(provider_codes),
                Provider.enabled.is_(True),
            )
        )
    ).all()
    provider_by_code = {provider.code: provider for provider in providers}

    active_query_ids = set(
        (
            await session.scalars(
                select(CollectionRun.search_query_id).where(
                    CollectionRun.search_query_id.in_(query_ids),
                    CollectionRun.status.in_(
                        (
                            CollectionStatus.PENDING.value,
                            CollectionStatus.LEASED.value,
                            CollectionStatus.RUNNING.value,
                        )
                    ),
                )
            )
        ).all()
    )
    created_count = 0
    for search_query_id in query_ids:
        if search_query_id in active_query_ids:
            continue
        search_query = query_by_id.get(search_query_id)
        if search_query is None:
            continue
        provider = provider_by_code.get(search_query.provider)
        if provider is None:
            continue
        idempotency_key = scheduled_idempotency_key(
            search_query_id=search_query.id,
            now=now,
            bucket_seconds=schedule_bucket_seconds,
        )
        statement = (
            pg_insert(CollectionRun)
            .values(
                id=uuid4(),
                search_query_id=search_query.id,
                provider_id=provider.id,
                idempotency_key=idempotency_key,
                status=CollectionStatus.PENDING.value,
                attempt=0,
                max_attempts=3,
                scheduled_at=now,
                run_metadata={
                    "trigger": "subscription_schedule",
                    "subscription_count": len(grouped[search_query_id]),
                    "schedule_bucket_seconds": schedule_bucket_seconds,
                },
            )
            .on_conflict_do_nothing(index_elements=[CollectionRun.idempotency_key])
            .returning(CollectionRun.id)
        )
        inserted = (await session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            created_count += 1
    return created_count


async def _recover_expired_leases(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> tuple[int, int]:
    expired_runs = (
        await session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.status.in_(
                    (CollectionStatus.LEASED.value, CollectionStatus.RUNNING.value)
                ),
                CollectionRun.lease_expires_at.is_not(None),
                CollectionRun.lease_expires_at <= now,
            )
            .order_by(CollectionRun.lease_expires_at, CollectionRun.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    ).all()
    exhausted_count = 0
    for run in expired_runs:
        previous_status = run.status
        recovery_metadata = dict((run.run_metadata or {}).get("recovery") or {})
        run.lease_owner = None
        run.lease_expires_at = None
        run.run_metadata = {
            **(run.run_metadata or {}),
            "recovery": {
                **recovery_metadata,
                "count": int(recovery_metadata.get("count", 0)) + 1,
                "last_recovered_at": now.isoformat(),
                "previous_status": previous_status,
            },
        }
        if run.attempt >= run.max_attempts:
            run.status = CollectionStatus.FAILED.value
            run.finished_at = now
            run.error_code = "lease_expired_attempts_exhausted"
            run.error_message = "Collection lease expired after all attempts"
            exhausted_count += 1
            continue
        run.status = CollectionStatus.PENDING.value
        run.scheduled_at = now
        run.started_at = None
        run.finished_at = None
        run.error_code = None
        run.error_message = None
    return len(expired_runs), exhausted_count

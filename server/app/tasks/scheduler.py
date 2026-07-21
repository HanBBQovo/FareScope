"""Celery Beat entry points for bounded collection scheduling and DB maintenance."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.partitions import (
    ensure_all_observation_partitions,
    maintain_observation_partition_lifecycle,
)
from app.db.session import create_engine, create_session_factory
from app.services.collection_dispatch import Publisher, publish_dispatch_leases_safely
from app.services.collection_scheduler import plan_scheduler_tick
from app.settings import Settings, get_settings
from app.tasks.celery_app import celery_app

_SCHEDULER_LOCK_ID = 6_323_717_466_347_023_757
_PARTITION_LOCK_ID = 6_323_717_466_347_023_758


async def run_scheduler_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    publisher: Publisher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create one run per due canonical query, then publish after the commit."""

    runtime_settings = settings or get_settings()
    current_time = now or datetime.now(UTC)
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(
            runtime_settings.database_url,
            echo=runtime_settings.database_echo,
            pool_size=min(runtime_settings.database_pool_size, 2),
            max_overflow=0,
            pool_timeout_seconds=runtime_settings.database_pool_timeout_seconds,
            pool_recycle_seconds=runtime_settings.database_pool_recycle_seconds,
            statement_timeout_ms=runtime_settings.database_statement_timeout_ms,
            application_name="farescope-scheduler",
        )
        session_factory = create_session_factory(owned_engine)

    try:
        async with session_factory() as session, session.begin():
            if not await _try_transaction_lock(session, _SCHEDULER_LOCK_ID):
                return {"status": "skipped", "reason": "scheduler_lock_busy"}
            plan = await plan_scheduler_tick(
                session,
                now=current_time,
                subscription_batch_size=(
                    runtime_settings.collection_scheduler_subscription_batch_size
                ),
                dispatch_batch_size=runtime_settings.collection_scheduler_dispatch_batch_size,
                dispatch_lease_seconds=runtime_settings.collection_dispatch_lease_seconds,
                schedule_bucket_seconds=runtime_settings.collection_schedule_bucket_seconds,
            )

        publish_results = await publish_dispatch_leases_safely(
            session_factory,
            plan.dispatch_leases,
            publisher=publisher,
        )
        return {
            "status": "ok",
            "due_subscription_count": plan.due_subscription_count,
            "grouped_query_count": plan.grouped_query_count,
            "created_run_count": plan.created_run_count,
            "recovered_run_count": plan.recovered_run_count,
            "exhausted_run_count": plan.exhausted_run_count,
            "leased_run_count": len(plan.dispatch_leases),
            "enqueued_run_count": sum(result.enqueued for result in publish_results),
            "publish_failure_count": sum(not result.enqueued for result in publish_results),
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


async def maintain_observation_partitions(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create current/near-future partitions separately from the hot scheduler tick."""

    runtime_settings = settings or get_settings()
    current_time = now or datetime.now(UTC)
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(
            runtime_settings.database_url,
            echo=runtime_settings.database_echo,
            pool_size=1,
            max_overflow=0,
            pool_timeout_seconds=runtime_settings.database_pool_timeout_seconds,
            pool_recycle_seconds=runtime_settings.database_pool_recycle_seconds,
            statement_timeout_ms=runtime_settings.database_statement_timeout_ms,
            application_name="farescope-partition-maintenance",
        )
        session_factory = create_session_factory(owned_engine)

    try:
        async with session_factory() as session, session.begin():
            if not await _try_transaction_lock(session, _PARTITION_LOCK_ID):
                return {"status": "skipped", "reason": "partition_lock_busy"}
            connection = await session.connection()
            partitions = await ensure_all_observation_partitions(
                connection,
                reference=current_time,
            )
            lifecycle_actions = await maintain_observation_partition_lifecycle(
                connection,
                reference=current_time,
                archive_after_months=(runtime_settings.collection_partition_archive_after_months),
                purge_after_months=(runtime_settings.collection_partition_purge_after_months),
                max_actions=runtime_settings.collection_partition_max_actions,
            )
        return {
            "status": "ok",
            "partition_count": sum(len(names) for names in partitions.values()),
            "tables": {table: list(names) for table, names in partitions.items()},
            "lifecycle_actions": [
                {
                    "action": item.action,
                    "table": item.parent_table,
                    "partition": item.partition_name,
                    "month": item.partition_month.isoformat(),
                }
                for item in lifecycle_actions
            ],
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


@celery_app.task(
    name="farescope.collection.scheduler_tick",
    ignore_result=False,
    soft_time_limit=50,
    time_limit=60,
)
def collection_scheduler_tick() -> dict[str, Any]:
    return asyncio.run(run_scheduler_tick())


@celery_app.task(
    name="farescope.collection.maintain_partitions",
    ignore_result=False,
    soft_time_limit=240,
    time_limit=300,
)
def collection_partition_maintenance() -> dict[str, Any]:
    return asyncio.run(maintain_observation_partitions())


async def _try_transaction_lock(session: AsyncSession, lock_id: int) -> bool:
    return bool(
        await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
    )

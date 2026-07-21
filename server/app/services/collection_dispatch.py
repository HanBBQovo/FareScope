from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CollectionRun
from app.models.enums import CollectionStatus
from app.services.collection_realtime import publish_collection_run_states_safely
from app.settings import Settings, get_settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_DISPATCH_OWNER_PREFIX = "dispatch:"


@dataclass(frozen=True, slots=True)
class DispatchLease:
    run_id: UUID
    token: str

    @property
    def lease_owner(self) -> str:
        return f"{_DISPATCH_OWNER_PREFIX}{self.token}"


@dataclass(frozen=True, slots=True)
class PublishResult:
    run_id: UUID
    enqueued: bool
    error_type: str | None = None


Publisher = Callable[[DispatchLease], object]


async def lease_collection_runs(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
    lease_seconds: int,
    run_id: UUID | None = None,
) -> tuple[DispatchLease, ...]:
    """Reserve pending runs for broker publication inside a short transaction."""

    if limit < 1:
        raise ValueError("dispatch limit must be positive")
    if lease_seconds < 1:
        raise ValueError("dispatch lease must be positive")

    statement = (
        select(CollectionRun)
        .where(
            CollectionRun.status == CollectionStatus.PENDING.value,
            CollectionRun.scheduled_at <= now,
            CollectionRun.attempt < CollectionRun.max_attempts,
        )
        .order_by(CollectionRun.scheduled_at, CollectionRun.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    if run_id is not None:
        statement = statement.where(CollectionRun.id == run_id)

    runs = (await session.scalars(statement)).all()
    leases: list[DispatchLease] = []
    for run in runs:
        token = uuid4().hex
        lease = DispatchLease(run_id=run.id, token=token)
        dispatch_metadata = dict((run.run_metadata or {}).get("dispatch") or {})
        run.status = CollectionStatus.LEASED.value
        run.lease_owner = lease.lease_owner
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.run_metadata = {
            **(run.run_metadata or {}),
            "dispatch": {
                **dispatch_metadata,
                "lease_count": int(dispatch_metadata.get("lease_count", 0)) + 1,
                "last_leased_at": now.isoformat(),
            },
        }
        leases.append(lease)
    await session.flush()
    return tuple(leases)


async def release_dispatch_leases(
    session: AsyncSession,
    leases: Sequence[DispatchLease],
    *,
    now: datetime,
    retry_delay_seconds: int = 30,
) -> int:
    """Return only still-owned dispatch leases to pending after publication failure."""

    if retry_delay_seconds < 0:
        raise ValueError("dispatch retry delay must not be negative")

    released = 0
    for lease in leases:
        run = await session.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.id == lease.run_id,
                CollectionRun.status == CollectionStatus.LEASED.value,
                CollectionRun.lease_owner == lease.lease_owner,
            )
            .with_for_update()
        )
        if run is None:
            continue
        dispatch_metadata = dict((run.run_metadata or {}).get("dispatch") or {})
        run.status = CollectionStatus.PENDING.value
        run.scheduled_at = now + timedelta(seconds=retry_delay_seconds)
        run.lease_owner = None
        run.lease_expires_at = None
        run.run_metadata = {
            **(run.run_metadata or {}),
            "dispatch": {
                **dispatch_metadata,
                "last_publish_failed_at": now.isoformat(),
            },
        }
        released += 1
    await session.flush()
    return released


def publish_collection_run(
    lease: DispatchLease,
    *,
    publisher: Publisher | None = None,
) -> PublishResult:
    """Publish to the collector queue without leaking broker details or raising."""

    try:
        (publisher or _publish_with_celery)(lease)
    except Exception as exc:  # noqa: BLE001 - broker failures are an expected boundary
        error_type = type(exc).__name__
        logger.warning(
            "collection dispatch publish failed",
            extra={"run_id": str(lease.run_id), "error_type": error_type},
        )
        return PublishResult(
            run_id=lease.run_id,
            enqueued=False,
            error_type=error_type,
        )
    return PublishResult(run_id=lease.run_id, enqueued=True)


async def dispatch_collection_run_safely(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    lease_seconds: int,
    publisher: Publisher | None = None,
    now: datetime | None = None,
    realtime_settings: Settings | None = None,
) -> PublishResult:
    """Lease after the caller commits, publish outside a transaction, and never raise."""

    current_time = now or datetime.now(UTC)
    try:
        async with session_factory() as session, session.begin():
            leases = await lease_collection_runs(
                session,
                now=current_time,
                limit=1,
                lease_seconds=lease_seconds,
                run_id=run_id,
            )
    except Exception as exc:  # noqa: BLE001 - on-demand collection must not break fare reads
        error_type = type(exc).__name__
        logger.warning(
            "collection dispatch lease failed",
            extra={"run_id": str(run_id), "error_type": error_type},
        )
        return PublishResult(run_id=run_id, enqueued=False, error_type=error_type)

    if not leases:
        return PublishResult(run_id=run_id, enqueued=False, error_type="run_not_pending")

    runtime_settings = realtime_settings or get_settings()
    await publish_collection_run_states_safely(
        session_factory,
        run_ids=tuple(lease.run_id for lease in leases),
        settings=runtime_settings,
    )
    results = await publish_dispatch_leases_safely(
        session_factory,
        leases,
        publisher=publisher,
        realtime_settings=runtime_settings,
    )
    return results[0]


async def publish_dispatch_leases_safely(
    session_factory: async_sessionmaker[AsyncSession],
    leases: Sequence[DispatchLease],
    *,
    publisher: Publisher | None = None,
    release_delay_seconds: int = 30,
    realtime_settings: Settings | None = None,
) -> tuple[PublishResult, ...]:
    """Publish already-committed leases and release broker failures without raising."""

    results: list[PublishResult] = []
    failed_leases: list[DispatchLease] = []
    for lease in leases:
        result = await asyncio.to_thread(
            publish_collection_run,
            lease,
            publisher=publisher,
        )
        results.append(result)
        if not result.enqueued:
            failed_leases.append(lease)

    if not failed_leases:
        return tuple(results)

    try:
        async with session_factory() as session, session.begin():
            await release_dispatch_leases(
                session,
                failed_leases,
                now=datetime.now(UTC),
                retry_delay_seconds=release_delay_seconds,
            )
        await publish_collection_run_states_safely(
            session_factory,
            run_ids=tuple(lease.run_id for lease in failed_leases),
            settings=realtime_settings or get_settings(),
        )
    except Exception as exc:  # noqa: BLE001 - leases expire and scheduler recovery remains valid
        logger.warning(
            "collection dispatch lease release failed",
            extra={
                "run_count": len(failed_leases),
                "error_type": type(exc).__name__,
            },
        )
    return tuple(results)


def dispatch_token_matches(*, lease_owner: str | None, dispatch_token: str | None) -> bool:
    if not lease_owner or not dispatch_token:
        return False
    return lease_owner == f"{_DISPATCH_OWNER_PREFIX}{dispatch_token}"


def _publish_with_celery(lease: DispatchLease) -> object:
    return celery_app.send_task(
        "farescope.collection.run",
        args=(str(lease.run_id),),
        kwargs={"dispatch_token": lease.token},
        queue="collector",
    )

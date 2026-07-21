from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, case, delete, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory_locks import (
    acquire_export_job_admission_lock,
    acquire_export_storage_reservation_lock,
    acquire_observation_partition_shared_lock,
)
from app.models import (
    ExportJob,
    ExportJobCollectionRun,
    SearchLeg,
    SearchQuery,
    Subscription,
)
from app.models.enums import ExportStatus
from app.services.export_data import list_missing_price_observation_source_months

ExportFormat = Literal["csv", "json"]


class ExportJobError(ValueError):
    pass


class ExportJobNotFoundError(ExportJobError):
    pass


class ExportIdempotencyConflictError(ExportJobError):
    pass


class ExportJobBusyError(ExportJobError):
    pass


class ExportSourceUnavailableError(ExportJobError):
    pass


class ExportStorageReservationError(ExportJobBusyError):
    pass


class ExportLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExportQueryContext:
    provider: str
    trip_type: str
    currency: str
    legs: tuple[tuple[int, str, str, str], ...]


@dataclass(frozen=True, slots=True)
class ExportWork:
    job_id: UUID
    user_id: UUID
    subscription_id: UUID
    search_query_id: UUID
    format: ExportFormat
    range_start: datetime
    range_end: datetime
    snapshot_at: datetime
    lease_owner: str
    attempt: int
    max_attempts: int
    context: ExportQueryContext


@dataclass(frozen=True, slots=True)
class ExportJobPage:
    items: tuple[ExportJob, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class ExportDispatchBatch:
    job_ids: tuple[UUID, ...]
    leased_until: datetime


@dataclass(frozen=True, slots=True)
class ExportQuotaUsage:
    active_jobs: int
    retained_files: int
    retained_bytes: int


_ACTIVE_EXPORT_STATUSES = (ExportStatus.PENDING.value, ExportStatus.RUNNING.value)
_RETAINED_EXPORT_STATUSES = (
    ExportStatus.SUCCEEDED.value,
    ExportStatus.EXPIRED.value,
    ExportStatus.DELETING.value,
)


def validate_export_range(
    range_start: datetime,
    range_end: datetime,
    *,
    max_range_days: int,
) -> tuple[datetime, datetime]:
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise ExportJobError("export timestamps must include a timezone offset")
    normalized_start = range_start.astimezone(UTC)
    normalized_end = range_end.astimezone(UTC)
    if normalized_start >= normalized_end:
        raise ExportJobError("rangeStart must precede rangeEnd")
    if normalized_end - normalized_start > timedelta(days=max_range_days):
        raise ExportJobError(f"export range cannot exceed {max_range_days} days")
    return normalized_start, normalized_end


def export_request_fingerprint(
    *,
    subscription_id: UUID,
    export_format: ExportFormat,
    range_start: datetime,
    range_end: datetime,
) -> str:
    payload = json.dumps(
        {
            "format": export_format,
            "range_end": range_end.astimezone(UTC).isoformat(),
            "range_start": range_start.astimezone(UTC).isoformat(),
            "subscription_id": str(subscription_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def create_export_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID,
    idempotency_key: str,
    export_format: ExportFormat,
    range_start: datetime,
    range_end: datetime,
    max_attempts: int,
    max_active_jobs: int,
    max_global_active_jobs: int,
    max_manifest_runs: int,
    max_file_bytes: int,
    max_retained_files: int,
    max_retained_bytes: int,
    dispatch_lease_seconds: int,
    now: datetime | None = None,
) -> tuple[ExportJob, bool]:
    subscription = await session.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id,
        )
    )
    if subscription is None:
        raise ExportJobNotFoundError("subscription not found")

    # Serialize task creation against archive lifecycle changes so an active range
    # cannot appear immediately after maintenance selected a partition for PURGE.
    await acquire_observation_partition_shared_lock(session)
    await acquire_export_job_admission_lock(session)
    await _acquire_export_quota_lock(session, user_id=user_id)

    fingerprint = export_request_fingerprint(
        subscription_id=subscription_id,
        export_format=export_format,
        range_start=range_start,
        range_end=range_end,
    )
    existing = await session.scalar(
        select(ExportJob).where(
            ExportJob.user_id == user_id,
            ExportJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise ExportIdempotencyConflictError(
                "idempotency key was already used for another export request"
            )
        return existing, False

    current_time = now or datetime.now(UTC)
    global_active_jobs = await session.scalar(
        select(func.count())
        .select_from(ExportJob)
        .where(ExportJob.status.in_(_ACTIVE_EXPORT_STATUSES))
    )
    if int(global_active_jobs or 0) >= max_global_active_jobs:
        raise ExportJobBusyError("the global active export limit has been reached")
    usage = await _load_export_quota_usage(session, user_id=user_id)
    if usage.active_jobs >= max_active_jobs:
        raise ExportJobBusyError("too many active export jobs")
    if usage.active_jobs + usage.retained_files + 1 > max_retained_files:
        raise ExportJobBusyError("export file quota reached; remove an older export first")
    projected_bytes = usage.retained_bytes + (usage.active_jobs + 1) * max_file_bytes
    if projected_bytes > max_retained_bytes:
        raise ExportJobBusyError("export storage quota reached; remove an older export first")

    job_id = uuid4()
    insert_result = await session.execute(
        postgresql_insert(ExportJob)
        .values(
            id=job_id,
            user_id=user_id,
            subscription_id=subscription.id,
            search_query_id=subscription.search_query_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            format=export_format,
            status=ExportStatus.PENDING.value,
            range_start=range_start,
            range_end=range_end,
            snapshot_at=current_time,
            attempt=0,
            max_attempts=max_attempts,
            reserved_bytes=0,
            available_at=current_time,
            dispatch_lease_expires_at=current_time + timedelta(seconds=dispatch_lease_seconds),
            processed_rows=0,
            created_at=current_time,
            updated_at=current_time,
        )
        .on_conflict_do_nothing(index_elements=("user_id", "idempotency_key"))
        .returning(ExportJob.id)
    )
    inserted_id = insert_result.scalar_one_or_none()
    if inserted_id is None:
        concurrent = await session.scalar(
            select(ExportJob).where(
                ExportJob.user_id == user_id,
                ExportJob.idempotency_key == idempotency_key,
            )
        )
        if concurrent is None:
            raise RuntimeError("export idempotency conflict could not be resolved")
        if concurrent.request_fingerprint != fingerprint:
            raise ExportIdempotencyConflictError(
                "idempotency key was already used for another export request"
            )
        return concurrent, False
    manifest_row = (
        (
            await session.execute(
                text(
                    """
                WITH candidate_runs AS (
                    SELECT id
                    FROM collection_runs
                    WHERE search_query_id = :search_query_id
                      AND status = 'succeeded'
                      AND finished_at >= :range_start
                      AND finished_at < :range_end
                    ORDER BY finished_at, id
                    LIMIT :candidate_limit
                ), manifest AS (
                    INSERT INTO export_job_collection_runs (
                        export_job_id,
                        collection_run_id
                    )
                    SELECT :job_id, id
                    FROM candidate_runs
                    RETURNING collection_run_id
                )
                UPDATE export_jobs
                SET snapshot_at = statement_timestamp()
                WHERE id = :job_id
                RETURNING snapshot_at, (SELECT count(*) FROM manifest) AS manifest_count
                """
                ),
                {
                    "candidate_limit": max_manifest_runs + 1,
                    "job_id": inserted_id,
                    "range_end": range_end,
                    "range_start": range_start,
                    "search_query_id": subscription.search_query_id,
                },
            )
        )
        .mappings()
        .one()
    )
    if int(manifest_row["manifest_count"]) > max_manifest_runs:
        raise ExportJobBusyError("export source run limit exceeded; choose a shorter date range")
    missing_source_months = await list_missing_price_observation_source_months(
        session,
        export_job_id=inserted_id,
    )
    if missing_source_months:
        missing_labels = ", ".join(month.strftime("%Y-%m") for month in missing_source_months)
        raise ExportSourceUnavailableError(
            "export source history is unavailable for UTC month(s): "
            f"{missing_labels}; choose a range within retained price history"
        )
    job = await session.get(ExportJob, inserted_id)
    if job is None:
        raise RuntimeError("created export job could not be loaded")
    return job, True


async def _acquire_export_quota_lock(session: AsyncSession, *, user_id: UUID) -> None:
    lock_name = f"farescope:export-quota:{user_id}"
    await session.scalar(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_name, 0))))


async def _load_export_quota_usage(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> ExportQuotaUsage:
    active = ExportJob.status.in_(_ACTIVE_EXPORT_STATUSES)
    retained = and_(
        ExportJob.status.in_(_RETAINED_EXPORT_STATUSES),
        ExportJob.file_name.is_not(None),
    )
    row = (
        await session.execute(
            select(
                func.count().filter(active),
                func.count().filter(retained),
                func.coalesce(
                    func.sum(
                        case(
                            (retained, func.coalesce(ExportJob.size_bytes, 0)),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(ExportJob.user_id == user_id)
        )
    ).one()
    return ExportQuotaUsage(
        active_jobs=int(row[0] or 0),
        retained_files=int(row[1] or 0),
        retained_bytes=int(row[2] or 0),
    )


async def assert_global_export_storage_capacity(
    session: AsyncSession,
    *,
    export_directory: str,
    additional_reservation_bytes: int,
    min_free_bytes: int,
    min_free_ratio: float,
    lock_already_acquired: bool = False,
) -> None:
    if not lock_already_acquired:
        await acquire_export_storage_reservation_lock(session)
    root = Path(export_directory).expanduser().resolve()
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    reserved_bytes = await session.scalar(
        select(func.coalesce(func.sum(ExportJob.reserved_bytes), 0)).where(
            ExportJob.status == ExportStatus.RUNNING.value
        )
    )
    minimum_free = max(min_free_bytes, math.ceil(usage.total * min_free_ratio))
    remaining = usage.free - int(reserved_bytes or 0) - additional_reservation_bytes
    if remaining < minimum_free:
        raise ExportStorageReservationError(
            "export storage is below its configured free-space reserve"
        )


async def list_active_export_ranges(
    session: AsyncSession,
) -> tuple[tuple[datetime, datetime], ...]:
    rows = await session.execute(
        select(ExportJob.range_start, ExportJob.range_end).where(
            ExportJob.status.in_(_ACTIVE_EXPORT_STATUSES)
        )
    )
    return tuple((row.range_start, row.range_end) for row in rows)


async def list_export_jobs(
    session: AsyncSession,
    *,
    user_id: UUID,
    as_of: datetime,
    limit: int,
    subscription_id: UUID | None = None,
    before_created_at: datetime | None = None,
    before_id: UUID | None = None,
) -> ExportJobPage:
    statement = select(ExportJob).where(
        ExportJob.user_id == user_id,
        ExportJob.created_at <= as_of,
    )
    if subscription_id is not None:
        statement = statement.where(ExportJob.subscription_id == subscription_id)
    if before_created_at is not None and before_id is not None:
        statement = statement.where(
            tuple_(ExportJob.created_at, ExportJob.id) < tuple_(before_created_at, before_id)
        )
    rows = (
        await session.scalars(
            statement.order_by(ExportJob.created_at.desc(), ExportJob.id.desc()).limit(limit + 1)
        )
    ).all()
    return ExportJobPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)


async def get_owned_export_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    job_id: UUID,
) -> ExportJob:
    job = await session.scalar(
        select(ExportJob).where(ExportJob.id == job_id, ExportJob.user_id == user_id)
    )
    if job is None:
        raise ExportJobNotFoundError("export job not found")
    return job


async def request_delete_export_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    job_id: UUID,
    now: datetime | None = None,
) -> str | None:
    current_time = now or datetime.now(UTC)
    job = await session.scalar(
        select(ExportJob)
        .where(ExportJob.id == job_id, ExportJob.user_id == user_id)
        .with_for_update()
    )
    if job is None:
        raise ExportJobNotFoundError("export job not found")
    if job.status == ExportStatus.RUNNING.value:
        raise ExportJobBusyError("a running export cannot be deleted")
    job.status = ExportStatus.DELETING.value
    job.lease_owner = None
    job.lease_expires_at = None
    job.dispatch_lease_expires_at = None
    job.dispatch_published_at = None
    job.reserved_bytes = 0
    job.updated_at = current_time
    await _delete_export_manifest(session, job_ids=(job.id,))
    return job.file_name


async def list_deleting_export_jobs(
    session: AsyncSession,
    *,
    limit: int,
) -> tuple[tuple[UUID, str | None], ...]:
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(ExportJob.status == ExportStatus.DELETING.value)
            .order_by(ExportJob.updated_at, ExportJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    return tuple((job.id, job.file_name) for job in jobs)


async def finalize_deleting_export_jobs(
    session: AsyncSession,
    *,
    job_ids: tuple[UUID, ...],
    user_id: UUID | None = None,
) -> int:
    if not job_ids:
        return 0
    statement = delete(ExportJob).where(
        ExportJob.id.in_(job_ids),
        ExportJob.status == ExportStatus.DELETING.value,
    )
    if user_id is not None:
        statement = statement.where(ExportJob.user_id == user_id)
    result = await session.execute(statement)
    return max(0, int(result.rowcount or 0))


async def list_referenced_export_file_names(
    session: AsyncSession,
) -> frozenset[str]:
    rows = await session.scalars(
        select(ExportJob.file_name).where(ExportJob.file_name.is_not(None))
    )
    return frozenset(str(value) for value in rows if value is not None)


async def claim_export_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    lease_owner: str,
    lease_seconds: int,
    reserved_bytes: int,
    now: datetime | None = None,
) -> ExportWork | None:
    current_time = now or datetime.now(UTC)
    job = await session.scalar(
        select(ExportJob)
        .where(
            ExportJob.id == job_id,
            or_(
                and_(
                    ExportJob.status == ExportStatus.PENDING.value,
                    ExportJob.available_at <= current_time,
                    ExportJob.attempt < ExportJob.max_attempts,
                ),
                and_(
                    ExportJob.status == ExportStatus.RUNNING.value,
                    ExportJob.lease_expires_at <= current_time,
                    ExportJob.attempt < ExportJob.max_attempts,
                ),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None

    subscription = None
    if job.subscription_id is not None:
        subscription = await session.scalar(
            select(Subscription).where(
                Subscription.id == job.subscription_id,
                Subscription.user_id == job.user_id,
                Subscription.search_query_id == job.search_query_id,
            )
        )
    if subscription is None:
        job.status = ExportStatus.FAILED.value
        job.error_code = "subscription_unavailable"
        job.error_message = "The subscription used for this export no longer exists."
        job.completed_at = current_time
        job.lease_owner = None
        job.lease_expires_at = None
        job.dispatch_lease_expires_at = None
        job.dispatch_published_at = None
        job.reserved_bytes = 0
        await _delete_export_manifest(session, job_ids=(job.id,))
        return None

    query = await session.get(SearchQuery, job.search_query_id)
    legs = (
        await session.scalars(
            select(SearchLeg)
            .where(SearchLeg.search_query_id == job.search_query_id)
            .order_by(SearchLeg.position)
            .limit(2)
        )
    ).all()
    if query is None or not legs:
        job.status = ExportStatus.FAILED.value
        job.error_code = "query_unavailable"
        job.error_message = "The search query used for this export is unavailable."
        job.completed_at = current_time
        job.lease_owner = None
        job.lease_expires_at = None
        job.dispatch_lease_expires_at = None
        job.dispatch_published_at = None
        job.reserved_bytes = 0
        await _delete_export_manifest(session, job_ids=(job.id,))
        return None

    job.status = ExportStatus.RUNNING.value
    job.reserved_bytes = reserved_bytes
    job.attempt += 1
    job.lease_owner = lease_owner
    job.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
    job.dispatch_lease_expires_at = None
    job.dispatch_published_at = None
    job.started_at = current_time
    job.completed_at = None
    job.processed_rows = 0
    job.error_code = None
    job.error_message = None
    return ExportWork(
        job_id=job.id,
        user_id=job.user_id,
        subscription_id=subscription.id,
        search_query_id=job.search_query_id,
        format=job.format,  # type: ignore[arg-type]
        range_start=job.range_start,
        range_end=job.range_end,
        snapshot_at=job.snapshot_at,
        lease_owner=lease_owner,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        context=ExportQueryContext(
            provider=query.provider,
            trip_type=query.trip_type,
            currency=query.currency,
            legs=tuple(
                (
                    leg.position,
                    leg.origin_code,
                    leg.destination_code,
                    leg.departure_date.isoformat(),
                )
                for leg in legs
            ),
        ),
    )


async def heartbeat_export_job(
    session: AsyncSession,
    *,
    work: ExportWork,
    processed_rows: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    result = await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id == work.job_id,
            ExportJob.status == ExportStatus.RUNNING.value,
            ExportJob.lease_owner == work.lease_owner,
            ExportJob.lease_expires_at > current_time,
        )
        .values(
            processed_rows=processed_rows,
            lease_expires_at=current_time + timedelta(seconds=lease_seconds),
            updated_at=current_time,
        )
    )
    if result.rowcount != 1:
        raise ExportLeaseLostError("export lease was lost")


async def defer_claimed_export_for_storage(
    session: AsyncSession,
    *,
    work: ExportWork,
    retry_seconds: int,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    result = await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id == work.job_id,
            ExportJob.status == ExportStatus.RUNNING.value,
            ExportJob.lease_owner == work.lease_owner,
            ExportJob.lease_expires_at > current_time,
            ExportJob.attempt == work.attempt,
        )
        .values(
            status=ExportStatus.PENDING.value,
            attempt=ExportJob.attempt - 1,
            available_at=current_time + timedelta(seconds=retry_seconds),
            processed_rows=0,
            reserved_bytes=0,
            error_code="insufficient_export_storage",
            error_message=("Export storage is temporarily busy. The job will retry automatically."),
            lease_owner=None,
            lease_expires_at=None,
            dispatch_lease_expires_at=None,
            dispatch_published_at=None,
            updated_at=current_time,
        )
    )
    if result.rowcount != 1:
        raise ExportLeaseLostError("export lease was lost")


async def finish_export_job(
    session: AsyncSession,
    *,
    work: ExportWork,
    row_count: int,
    file_name: str,
    content_type: str,
    size_bytes: int,
    checksum_sha256: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    result = await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id == work.job_id,
            ExportJob.status == ExportStatus.RUNNING.value,
            ExportJob.lease_owner == work.lease_owner,
            ExportJob.lease_expires_at > current_time,
        )
        .values(
            status=ExportStatus.SUCCEEDED.value,
            completed_at=current_time,
            expires_at=current_time + timedelta(seconds=ttl_seconds),
            processed_rows=row_count,
            row_count=row_count,
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            reserved_bytes=0,
            error_code=None,
            error_message=None,
            lease_owner=None,
            lease_expires_at=None,
            dispatch_lease_expires_at=None,
            dispatch_published_at=None,
            updated_at=current_time,
        )
    )
    if result.rowcount != 1:
        raise ExportLeaseLostError("export lease was lost")
    await _delete_export_manifest(session, job_ids=(work.job_id,))


async def fail_export_job(
    session: AsyncSession,
    *,
    work: ExportWork,
    error_code: str,
    error_message: str,
    retry_base_seconds: int,
    permanent: bool,
    now: datetime | None = None,
) -> str:
    current_time = now or datetime.now(UTC)
    retry = not permanent and work.attempt < work.max_attempts
    next_status = ExportStatus.PENDING.value if retry else ExportStatus.FAILED.value
    values: dict[str, object] = {
        "status": next_status,
        "error_code": error_code[:120],
        "error_message": error_message[:1000],
        "lease_owner": None,
        "lease_expires_at": None,
        "dispatch_lease_expires_at": None,
        "dispatch_published_at": None,
        "reserved_bytes": 0,
        "updated_at": current_time,
    }
    if retry:
        values["available_at"] = current_time + timedelta(
            seconds=retry_base_seconds * (2 ** max(0, work.attempt - 1))
        )
        values["processed_rows"] = 0
    else:
        values["completed_at"] = current_time
    result = await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id == work.job_id,
            ExportJob.status == ExportStatus.RUNNING.value,
            ExportJob.lease_owner == work.lease_owner,
            ExportJob.lease_expires_at > current_time,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise ExportLeaseLostError("export lease was lost")
    if not retry:
        await _delete_export_manifest(session, job_ids=(work.job_id,))
    return next_status


async def lease_dispatchable_export_ids(
    session: AsyncSession,
    *,
    limit: int,
    dispatch_lease_seconds: int,
    now: datetime | None = None,
) -> ExportDispatchBatch:
    current_time = now or datetime.now(UTC)
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(
                or_(
                    and_(
                        ExportJob.status == ExportStatus.PENDING.value,
                        ExportJob.available_at <= current_time,
                        ExportJob.attempt < ExportJob.max_attempts,
                    ),
                    and_(
                        ExportJob.status == ExportStatus.RUNNING.value,
                        ExportJob.lease_expires_at <= current_time,
                        ExportJob.attempt < ExportJob.max_attempts,
                    ),
                ),
                ExportJob.dispatch_published_at.is_(None),
                or_(
                    ExportJob.dispatch_lease_expires_at.is_(None),
                    ExportJob.dispatch_lease_expires_at <= current_time,
                ),
            )
            .order_by(ExportJob.available_at, ExportJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    dispatch_until = current_time + timedelta(seconds=dispatch_lease_seconds)
    for job in jobs:
        job.dispatch_lease_expires_at = dispatch_until
    return ExportDispatchBatch(
        job_ids=tuple(job.id for job in jobs),
        leased_until=dispatch_until,
    )


async def mark_export_dispatch_published(
    session: AsyncSession,
    *,
    job_ids: tuple[UUID, ...],
    leased_until: datetime,
    now: datetime | None = None,
) -> int:
    if not job_ids:
        return 0
    current_time = now or datetime.now(UTC)
    result = await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id.in_(job_ids),
            ExportJob.dispatch_published_at.is_(None),
            ExportJob.dispatch_lease_expires_at == leased_until,
        )
        .values(
            dispatch_published_at=current_time,
            dispatch_lease_expires_at=None,
            updated_at=current_time,
        )
    )
    return max(0, int(result.rowcount or 0))


async def fail_stale_pending_export_jobs(
    session: AsyncSession,
    *,
    timeout_seconds: int,
    limit: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(
                ExportJob.status == ExportStatus.PENDING.value,
                ExportJob.created_at <= current_time - timedelta(seconds=timeout_seconds),
            )
            .order_by(ExportJob.created_at, ExportJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        job.status = ExportStatus.FAILED.value
        job.completed_at = current_time
        job.error_code = "queue_timeout"
        job.error_message = "The export stayed queued too long. Delete it and create a new export."
        job.reserved_bytes = 0
        job.dispatch_lease_expires_at = None
        job.dispatch_published_at = None
        job.lease_owner = None
        job.lease_expires_at = None
    await _delete_export_manifest(session, job_ids=tuple(job.id for job in jobs))
    return len(jobs)


async def fail_exhausted_export_jobs(
    session: AsyncSession,
    *,
    limit: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(
                ExportJob.status == ExportStatus.RUNNING.value,
                ExportJob.lease_expires_at <= current_time,
                ExportJob.attempt >= ExportJob.max_attempts,
            )
            .order_by(ExportJob.lease_expires_at, ExportJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        job.status = ExportStatus.FAILED.value
        job.completed_at = current_time
        job.error_code = "attempts_exhausted"
        job.error_message = "The export worker stopped before the file was completed."
        job.lease_owner = None
        job.lease_expires_at = None
        job.dispatch_lease_expires_at = None
        job.dispatch_published_at = None
        job.reserved_bytes = 0
    await _delete_export_manifest(session, job_ids=tuple(job.id for job in jobs))
    return len(jobs)


async def expire_export_jobs(
    session: AsyncSession,
    *,
    limit: int,
    now: datetime | None = None,
) -> tuple[tuple[UUID, str], ...]:
    current_time = now or datetime.now(UTC)
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(
                or_(
                    and_(
                        ExportJob.status == ExportStatus.SUCCEEDED.value,
                        ExportJob.expires_at <= current_time,
                    ),
                    and_(
                        ExportJob.status == ExportStatus.EXPIRED.value,
                        ExportJob.file_name.is_not(None),
                    ),
                )
            )
            .order_by(ExportJob.expires_at, ExportJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    files: list[tuple[UUID, str]] = []
    for job in jobs:
        job.status = ExportStatus.EXPIRED.value
        job.lease_owner = None
        job.lease_expires_at = None
        job.dispatch_lease_expires_at = None
        job.dispatch_published_at = None
        if job.file_name is not None:
            files.append((job.id, job.file_name))
    return tuple(files)


async def mark_export_files_removed(
    session: AsyncSession,
    *,
    job_ids: tuple[UUID, ...],
) -> None:
    if not job_ids:
        return
    await session.execute(
        update(ExportJob)
        .where(
            ExportJob.id.in_(job_ids),
            ExportJob.status == ExportStatus.EXPIRED.value,
        )
        .values(file_name=None, content_type=None)
    )


async def _delete_export_manifest(
    session: AsyncSession,
    *,
    job_ids: tuple[UUID, ...],
) -> None:
    if not job_ids:
        return
    await session.execute(
        delete(ExportJobCollectionRun).where(ExportJobCollectionRun.export_job_id.in_(job_ids))
    )

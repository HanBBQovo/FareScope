from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import create_engine, create_session_factory
from app.services.export_files import (
    ExportFileError,
    discover_stale_export_artifacts,
    generate_export_file,
    remove_export_artifact,
    remove_export_file,
)
from app.services.export_jobs import (
    ExportLeaseLostError,
    ExportStorageReservationError,
    assert_global_export_storage_capacity,
    claim_export_job,
    defer_claimed_export_for_storage,
    expire_export_jobs,
    fail_exhausted_export_jobs,
    fail_export_job,
    fail_stale_pending_export_jobs,
    finalize_deleting_export_jobs,
    finish_export_job,
    lease_dispatchable_export_ids,
    list_deleting_export_jobs,
    list_referenced_export_file_names,
    mark_export_dispatch_published,
    mark_export_files_removed,
)
from app.settings import Settings, get_settings
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def run_export_job_once(
    job_id: UUID,
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    runtime_settings = settings or get_settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(
            runtime_settings.database_url,
            echo=runtime_settings.database_echo,
            pool_size=runtime_settings.database_pool_size,
            max_overflow=runtime_settings.database_max_overflow,
            pool_timeout_seconds=runtime_settings.database_pool_timeout_seconds,
            pool_recycle_seconds=runtime_settings.database_pool_recycle_seconds,
            statement_timeout_ms=runtime_settings.database_statement_timeout_ms,
            application_name="farescope-exports",
        )
        session_factory = create_session_factory(owned_engine)

    lease_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
    work = None
    storage_deferred = False
    generated = None
    try:
        async with session_factory() as session, session.begin():
            claimed_work = await claim_export_job(
                session,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_seconds=runtime_settings.export_lease_seconds,
                reserved_bytes=runtime_settings.export_max_file_bytes,
            )
            if claimed_work is not None:
                try:
                    await assert_global_export_storage_capacity(
                        session,
                        export_directory=runtime_settings.export_directory,
                        additional_reservation_bytes=0,
                        min_free_bytes=runtime_settings.export_min_free_bytes,
                        min_free_ratio=runtime_settings.export_min_free_ratio,
                    )
                except ExportStorageReservationError:
                    await defer_claimed_export_for_storage(
                        session,
                        work=claimed_work,
                        retry_seconds=max(
                            runtime_settings.export_retry_base_seconds,
                            runtime_settings.export_lease_seconds,
                        ),
                    )
                    storage_deferred = True
                else:
                    work = claimed_work
        if storage_deferred:
            logger.warning("export_job_storage_deferred", job_id=str(job_id))
            return {
                "job_id": str(job_id),
                "claimed": False,
                "status": "deferred",
                "error_code": "insufficient_export_storage",
            }
        if work is None:
            return {"job_id": str(job_id), "claimed": False}

        generated = await generate_export_file(
            session_factory,
            work=work,
            directory=runtime_settings.export_directory,
            max_rows=runtime_settings.export_max_rows,
            max_file_bytes=runtime_settings.export_max_file_bytes,
            min_free_bytes=runtime_settings.export_min_free_bytes,
            min_free_ratio=runtime_settings.export_min_free_ratio,
            page_size=runtime_settings.export_page_size,
            lease_seconds=runtime_settings.export_lease_seconds,
        )
        async with session_factory() as session, session.begin():
            await finish_export_job(
                session,
                work=work,
                row_count=generated.row_count,
                file_name=generated.file_name,
                content_type=generated.content_type,
                size_bytes=generated.size_bytes,
                checksum_sha256=generated.checksum_sha256,
                ttl_seconds=runtime_settings.export_file_ttl_seconds,
            )
        return {
            "job_id": str(job_id),
            "claimed": True,
            "status": "succeeded",
            "rows": generated.row_count,
            "size_bytes": generated.size_bytes,
        }
    except ExportLeaseLostError:
        if generated is not None:
            await _remove_file_without_raising(
                runtime_settings.export_directory,
                generated.file_name,
                job_id=job_id,
                reason="lease_lost",
            )
        return {"job_id": str(job_id), "claimed": True, "status": "lease_lost"}
    except Exception as error:
        if work is None:
            logger.exception("export_job_claim_failed", job_id=str(job_id))
            raise
        if generated is not None:
            await _remove_file_without_raising(
                runtime_settings.export_directory,
                generated.file_name,
                job_id=job_id,
                reason="generation_failed",
            )
        permanent = isinstance(error, ExportFileError) and error.permanent
        error_code = error.code if isinstance(error, ExportFileError) else "generation_failed"
        message = (
            str(error)
            if isinstance(error, ExportFileError)
            else "Export generation failed and will be retried when possible."
        )
        async with session_factory() as session, session.begin():
            next_status = await fail_export_job(
                session,
                work=work,
                error_code=error_code,
                error_message=message,
                retry_base_seconds=runtime_settings.export_retry_base_seconds,
                permanent=permanent,
            )
        logger.exception(
            "export_job_failed",
            job_id=str(job_id),
            error_code=error_code,
            next_status=next_status,
        )
        return {
            "job_id": str(job_id),
            "claimed": True,
            "status": next_status,
            "error_code": error_code,
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


async def maintain_exports_once(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    runtime_settings = settings or get_settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(
            runtime_settings.database_url,
            echo=runtime_settings.database_echo,
            pool_size=runtime_settings.database_pool_size,
            max_overflow=runtime_settings.database_max_overflow,
            pool_timeout_seconds=runtime_settings.database_pool_timeout_seconds,
            pool_recycle_seconds=runtime_settings.database_pool_recycle_seconds,
            statement_timeout_ms=runtime_settings.database_statement_timeout_ms,
            application_name="farescope-export-maintenance",
        )
        session_factory = create_session_factory(owned_engine)
    try:
        async with session_factory() as session, session.begin():
            queue_timed_out = await fail_stale_pending_export_jobs(
                session,
                timeout_seconds=runtime_settings.export_pending_timeout_seconds,
                limit=runtime_settings.export_dispatch_batch_size,
            )
            exhausted = await fail_exhausted_export_jobs(
                session,
                limit=runtime_settings.export_dispatch_batch_size,
            )
            expired_files = await expire_export_jobs(
                session,
                limit=runtime_settings.export_dispatch_batch_size,
            )
            deleting_files = await list_deleting_export_jobs(
                session,
                limit=runtime_settings.export_dispatch_batch_size,
            )

        cleanup_failures = 0
        expired_removed_ids: list[UUID] = []
        for expired_job_id, file_name in expired_files:
            if await _remove_file_without_raising(
                runtime_settings.export_directory,
                file_name,
                job_id=expired_job_id,
                reason="expired",
            ):
                expired_removed_ids.append(expired_job_id)
            else:
                cleanup_failures += 1

        deleting_removed_ids: list[UUID] = []
        for deleting_job_id, file_name in deleting_files:
            if file_name is None or await _remove_file_without_raising(
                runtime_settings.export_directory,
                file_name,
                job_id=deleting_job_id,
                reason="deleting",
            ):
                deleting_removed_ids.append(deleting_job_id)
            else:
                cleanup_failures += 1

        async with session_factory() as session, session.begin():
            await mark_export_files_removed(
                session,
                job_ids=tuple(expired_removed_ids),
            )
            deleted = await finalize_deleting_export_jobs(
                session,
                job_ids=tuple(deleting_removed_ids),
            )

        orphan_removed = 0
        try:
            async with session_factory() as session:
                referenced_names = await list_referenced_export_file_names(session)
            orphan_candidates = await asyncio.to_thread(
                discover_stale_export_artifacts,
                runtime_settings.export_directory,
                referenced_file_names=referenced_names,
                older_than=datetime.now(UTC)
                - timedelta(seconds=runtime_settings.export_orphan_grace_seconds),
                limit=runtime_settings.export_orphan_cleanup_batch_size,
            )
            for candidate in orphan_candidates:
                try:
                    await asyncio.to_thread(
                        remove_export_artifact,
                        runtime_settings.export_directory,
                        candidate.file_name,
                    )
                    orphan_removed += 1
                except (OSError, ValueError) as error:
                    cleanup_failures += 1
                    logger.warning(
                        "export_orphan_cleanup_failed",
                        file_name=candidate.file_name,
                        temporary=candidate.temporary,
                        error_type=type(error).__name__,
                    )
        except (OSError, ValueError) as error:
            cleanup_failures += 1
            logger.warning(
                "export_orphan_discovery_failed",
                error_type=type(error).__name__,
            )

        async with session_factory() as session, session.begin():
            dispatch_batch = await lease_dispatchable_export_ids(
                session,
                limit=runtime_settings.export_dispatch_batch_size,
                dispatch_lease_seconds=runtime_settings.export_dispatch_lease_seconds,
            )
        dispatch_results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    enqueue_export_job,
                    pending_id,
                )
                for pending_id in dispatch_batch.job_ids
            )
        )
        published_ids = tuple(
            job_id
            for job_id, published in zip(
                dispatch_batch.job_ids,
                dispatch_results,
                strict=True,
            )
            if published
        )
        async with session_factory() as session, session.begin():
            published = await mark_export_dispatch_published(
                session,
                job_ids=published_ids,
                leased_until=dispatch_batch.leased_until,
            )
        return {
            "expired": len(expired_files),
            "failed_exhausted": exhausted,
            "queue_timed_out": queue_timed_out,
            "removed": len(expired_removed_ids),
            "delete_requested": len(deleting_files),
            "deleted": deleted,
            "orphan_removed": orphan_removed,
            "cleanup_failures": cleanup_failures,
            "dispatched": published,
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


def enqueue_export_job(job_id: UUID) -> bool:
    try:
        celery_app.send_task("farescope.exports.run", args=(str(job_id),))
    except Exception:
        logger.exception("export_job_enqueue_failed", job_id=str(job_id))
        return False
    return True


async def _remove_file_without_raising(
    directory: str,
    file_name: str,
    *,
    job_id: UUID,
    reason: str,
) -> bool:
    """Treat an absent file as cleaned; callers must share the export volume namespace."""

    try:
        await asyncio.to_thread(remove_export_file, directory, file_name)
    except (OSError, ValueError) as error:
        logger.warning(
            "export_file_cleanup_deferred",
            job_id=str(job_id),
            reason=reason,
            error_type=type(error).__name__,
        )
        return False
    return True


@celery_app.task(name="farescope.exports.run", ignore_result=False)
def run_export_job(job_id: str) -> dict[str, Any]:
    return asyncio.run(run_export_job_once(UUID(job_id)))


@celery_app.task(name="farescope.exports.maintain", ignore_result=False)
def maintain_exports() -> dict[str, Any]:
    return asyncio.run(maintain_exports_once())

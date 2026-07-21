from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    DatabaseSession,
    FunctionDatabaseSession,
    IdentityDependency,
    SettingsDependency,
    require_csrf,
)
from app.api.pagination import (
    InvalidCursorError,
    TimestampCursor,
    decode_timestamp_cursor,
    encode_timestamp_cursor,
)
from app.api.schemas.exports import (
    ExportJobCreateRequest,
    ExportJobListResponse,
    ExportJobPublic,
)
from app.api.schemas.fares import ResponseMeta
from app.models import ExportJob
from app.models.enums import ExportStatus
from app.services.export_files import (
    iter_open_export_file,
    open_export_file,
    remove_export_file,
)
from app.services.export_jobs import (
    ExportIdempotencyConflictError,
    ExportJobBusyError,
    ExportJobError,
    ExportJobNotFoundError,
    create_export_job,
    finalize_deleting_export_jobs,
    get_owned_export_job,
    list_export_jobs,
    mark_export_dispatch_published,
    request_delete_export_job,
    validate_export_range,
)
from app.tasks.exports import enqueue_export_job

router = APIRouter()
logger = structlog.get_logger(__name__)


def _serialize_export_job(job: ExportJob, *, now: datetime | None = None) -> ExportJobPublic:
    current_time = now or datetime.now(UTC)
    download_ready = bool(
        job.status == ExportStatus.SUCCEEDED.value
        and job.expires_at is not None
        and job.expires_at > current_time
        and job.file_name
        and job.size_bytes is not None
        and job.checksum_sha256
    )
    return ExportJobPublic(
        id=job.id,
        subscriptionId=job.subscription_id,
        format=job.format,
        scope="canonical_query",
        status=job.status,
        rangeStart=job.range_start,
        rangeEnd=job.range_end,
        snapshotAt=job.snapshot_at,
        attempt=job.attempt,
        maxAttempts=job.max_attempts,
        processedRows=job.processed_rows,
        rowCount=job.row_count,
        sizeBytes=job.size_bytes,
        checksumSha256=job.checksum_sha256,
        fileName=job.file_name,
        errorCode=job.error_code,
        errorMessage=job.error_message,
        createdAt=job.created_at,
        startedAt=job.started_at,
        completedAt=job.completed_at,
        expiresAt=job.expires_at,
        downloadReady=download_ready,
    )


@router.post(
    "",
    response_model=ExportJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def create_price_export(
    payload: ExportJobCreateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ExportJobPublic:
    try:
        range_start, range_end = validate_export_range(
            payload.range_start,
            payload.range_end,
            max_range_days=settings.export_max_range_days,
        )
        async with database.begin():
            job, created = await create_export_job(
                database,
                user_id=identity.user.id,
                subscription_id=payload.subscription_id,
                idempotency_key=payload.idempotency_key,
                export_format=payload.format,
                range_start=range_start,
                range_end=range_end,
                max_attempts=settings.export_max_attempts,
                max_active_jobs=settings.export_max_active_jobs,
                max_global_active_jobs=settings.export_global_max_active_jobs,
                max_manifest_runs=settings.export_manifest_max_runs,
                max_file_bytes=settings.export_max_file_bytes,
                max_retained_files=settings.export_user_max_retained_files,
                max_retained_bytes=settings.export_user_max_retained_bytes,
                dispatch_lease_seconds=settings.export_dispatch_lease_seconds,
            )
    except ExportJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ExportIdempotencyConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ExportJobBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
        ) from error
    except ExportJobError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if created:
        leased_until = job.dispatch_lease_expires_at
        published = await asyncio.to_thread(enqueue_export_job, job.id)
        if published and leased_until is not None:
            async with database.begin():
                await mark_export_dispatch_published(
                    database,
                    job_ids=(job.id,),
                    leased_until=leased_until,
                )
    return _serialize_export_job(job)


@router.get("", response_model=ExportJobListResponse)
async def list_price_exports(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    subscription_id: Annotated[UUID | None, Query(alias="subscriptionId")] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ExportJobListResponse:
    as_of = datetime.now(UTC)
    before_created_at = None
    before_id = None
    if cursor is not None:
        try:
            decoded = decode_timestamp_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        as_of = decoded.as_of
        before_created_at = decoded.timestamp
        before_id = decoded.row_id
    page = await list_export_jobs(
        database,
        user_id=identity.user.id,
        as_of=as_of,
        limit=limit,
        subscription_id=subscription_id,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = None
    if page.has_more and page.items:
        last = page.items[-1]
        next_cursor = encode_timestamp_cursor(
            TimestampCursor(as_of=as_of, timestamp=last.created_at, row_id=last.id)
        )
    generated_at = datetime.now(UTC)
    return ExportJobListResponse(
        meta=ResponseMeta(generatedAt=generated_at),
        items=[_serialize_export_job(job, now=generated_at) for job in page.items],
        hasMore=page.has_more,
        nextCursor=next_cursor,
    )


@router.get("/{job_id}", response_model=ExportJobPublic)
async def get_price_export(
    job_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> ExportJobPublic:
    try:
        job = await get_owned_export_job(database, user_id=identity.user.id, job_id=job_id)
    except ExportJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _serialize_export_job(job)


@router.get("/{job_id}/download", response_class=StreamingResponse)
async def download_price_export(
    job_id: UUID,
    identity: IdentityDependency,
    database: FunctionDatabaseSession,
    settings: SettingsDependency,
) -> StreamingResponse:
    try:
        job = await get_owned_export_job(database, user_id=identity.user.id, job_id=job_id)
    except ExportJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    now = datetime.now(UTC)
    if job.status != ExportStatus.SUCCEEDED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="export is not ready")
    if job.expires_at is None or job.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="export file has expired")
    if job.file_name is None or job.size_bytes is None or job.content_type is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="export file is unavailable")
    try:
        file = open_export_file(
            settings.export_directory,
            job.file_name,
            expected_size=job.size_bytes,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="export file is unavailable",
        ) from error
    encoded_name = quote(job.file_name, safe="")
    return StreamingResponse(
        iter_open_export_file(file),
        media_type=job.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=utf-8''{encoded_name}",
            "Content-Length": str(job.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    responses={status.HTTP_202_ACCEPTED: {"description": "File cleanup is pending"}},
)
async def remove_price_export(
    job_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    try:
        async with database.begin():
            file_name = await request_delete_export_job(
                database,
                user_id=identity.user.id,
                job_id=job_id,
            )
    except ExportJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ExportJobBusyError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    cleanup_pending = False
    if file_name is not None:
        try:
            await asyncio.to_thread(
                remove_export_file,
                settings.export_directory,
                file_name,
            )
        except (OSError, ValueError) as error:
            cleanup_pending = True
            logger.warning(
                "export_delete_cleanup_deferred",
                job_id=str(job_id),
                error_type=type(error).__name__,
            )
    if cleanup_pending:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    async with database.begin():
        await finalize_deleting_export_jobs(
            database,
            job_ids=(job_id,),
            user_id=identity.user.id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

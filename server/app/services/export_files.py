from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.export_data import ExportObservation, iter_export_observation_pages
from app.services.export_jobs import ExportWork, heartbeat_export_job

_EXPORT_FILE_NAME = re.compile(
    r"fare-export-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"-[0-9a-f]{32}\.(csv|json)"
)
_EXPORT_TEMP_FILE_NAME = re.compile(
    r"\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"\.[0-9a-f]{32}\.tmp"
)
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
logger = structlog.get_logger(__name__)


class ExportFileError(RuntimeError):
    code = "file_generation_failed"
    permanent = False


class ExportRowLimitError(ExportFileError):
    code = "row_limit_exceeded"
    permanent = True


class ExportFileSizeLimitError(ExportFileError):
    code = "file_size_limit_exceeded"
    permanent = True


class ExportStoragePressureError(ExportFileError):
    code = "insufficient_export_storage"
    permanent = False


@dataclass(frozen=True, slots=True)
class GeneratedExport:
    file_name: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class ExportArtifactCandidate:
    file_name: str
    temporary: bool


class _BoundedBinaryWriter:
    def __init__(self, path: Path, *, max_bytes: int) -> None:
        self._file = path.open("xb")
        self._max_bytes = max_bytes
        self._size = 0
        self._hasher = hashlib.sha256()

    @property
    def size(self) -> int:
        return self._size

    @property
    def checksum(self) -> str:
        return self._hasher.hexdigest()

    def write_text(self, value: str) -> None:
        payload = value.encode("utf-8")
        if self._size + len(payload) > self._max_bytes:
            raise ExportFileSizeLimitError("export file exceeds the configured size limit")
        self._file.write(payload)
        self._hasher.update(payload)
        self._size += len(payload)

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()


def sanitize_csv_cell(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if value.startswith(("\t", "\r")) or stripped.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def export_file_name(job_id: UUID, export_format: str, *, nonce: str) -> str:
    if export_format not in {"csv", "json"}:
        raise ValueError("unsupported export format")
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("invalid export file nonce")
    return f"fare-export-{job_id}-{nonce}.{export_format}"


def resolve_export_file_path(directory: str | Path, file_name: str) -> Path:
    if _EXPORT_FILE_NAME.fullmatch(file_name) is None or Path(file_name).name != file_name:
        raise ValueError("invalid export file name")
    root = Path(directory).expanduser().resolve()
    candidate = (root / file_name).resolve()
    if candidate.parent != root:
        raise ValueError("export file escapes the configured directory")
    return candidate


def resolve_export_artifact_path(directory: str | Path, file_name: str) -> Path:
    if (
        _EXPORT_FILE_NAME.fullmatch(file_name) is None
        and _EXPORT_TEMP_FILE_NAME.fullmatch(file_name) is None
    ) or Path(file_name).name != file_name:
        raise ValueError("invalid export artifact name")
    root = Path(directory).expanduser().resolve()
    candidate = (root / file_name).resolve()
    if candidate.parent != root:
        raise ValueError("export artifact escapes the configured directory")
    return candidate


def remove_export_file(directory: str | Path, file_name: str) -> bool:
    path = resolve_export_file_path(directory, file_name)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def open_export_file(
    directory: str | Path,
    file_name: str,
    *,
    expected_size: int,
) -> BinaryIO:
    path = resolve_export_file_path(directory, file_name)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
            raise ValueError("export file is incomplete")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def iter_open_export_file(
    file: BinaryIO,
    *,
    chunk_size: int = 64 * 1024,
) -> Iterator[bytes]:
    try:
        while chunk := file.read(chunk_size):
            yield chunk
    finally:
        file.close()


def remove_export_artifact(directory: str | Path, file_name: str) -> bool:
    path = resolve_export_artifact_path(directory, file_name)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def discover_stale_export_artifacts(
    directory: str | Path,
    *,
    referenced_file_names: frozenset[str],
    older_than: datetime,
    limit: int,
) -> tuple[ExportArtifactCandidate, ...]:
    if limit < 1:
        raise ValueError("artifact cleanup limit must be positive")
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        return ()
    candidates: list[ExportArtifactCandidate] = []
    with os.scandir(root) as entries:
        for entry in entries:
            is_final = _EXPORT_FILE_NAME.fullmatch(entry.name) is not None
            is_temporary = _EXPORT_TEMP_FILE_NAME.fullmatch(entry.name) is not None
            if not is_final and not is_temporary:
                continue
            if is_final and entry.name in referenced_file_names:
                continue
            try:
                metadata = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if metadata.st_mtime > older_than.timestamp():
                continue
            candidates.append(
                ExportArtifactCandidate(file_name=entry.name, temporary=is_temporary)
            )
            if len(candidates) >= limit:
                break
    return tuple(candidates)


def ensure_export_storage_capacity(
    directory: str | Path,
    *,
    reservation_bytes: int,
    min_free_bytes: int,
    min_free_ratio: float,
) -> None:
    usage = shutil.disk_usage(Path(directory).expanduser().resolve())
    remaining = usage.free - reservation_bytes
    if remaining < min_free_bytes or (
        usage.total > 0 and remaining / usage.total < min_free_ratio
    ):
        raise ExportStoragePressureError(
            "export storage is below its configured free-space reserve"
        )


async def generate_export_file(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    work: ExportWork,
    directory: str,
    max_rows: int,
    max_file_bytes: int,
    min_free_bytes: int,
    min_free_ratio: float,
    page_size: int,
    lease_seconds: int,
) -> GeneratedExport:
    root = Path(directory).expanduser().resolve()
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    ensure_export_storage_capacity(
        root,
        reservation_bytes=max_file_bytes,
        min_free_bytes=min_free_bytes,
        min_free_ratio=min_free_ratio,
    )
    file_nonce = uuid4().hex
    file_name = export_file_name(work.job_id, work.format, nonce=file_nonce)
    final_path = resolve_export_file_path(root, file_name)
    temp_path = root / f".{work.job_id}.{file_nonce}.tmp"
    writer: _BoundedBinaryWriter | None = None
    row_count = 0
    final_installed = False
    try:
        writer = _BoundedBinaryWriter(temp_path, max_bytes=max_file_bytes)
        os.chmod(temp_path, 0o640)
        if work.format == "csv":
            _write_csv_header(writer)
        else:
            _write_json_header(writer, work)

        async for page in iter_export_observation_pages(
            session_factory,
            job_id=work.job_id,
            search_query_id=work.search_query_id,
            range_start=work.range_start,
            range_end=work.range_end,
            page_size=page_size,
        ):
            for observation in page:
                if row_count >= max_rows:
                    raise ExportRowLimitError("export row count exceeds the configured limit")
                if work.format == "csv":
                    _write_csv_observation(writer, work, observation)
                else:
                    if row_count:
                        writer.write_text(",")
                    writer.write_text(
                        json.dumps(
                            _json_observation(observation),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                row_count += 1

            async with session_factory() as session, session.begin():
                await heartbeat_export_job(
                    session,
                    work=work,
                    processed_rows=row_count,
                    lease_seconds=lease_seconds,
                )

        if work.format == "json":
            writer.write_text("]}")
        writer.close()
        os.replace(temp_path, final_path)
        final_installed = True
        _sync_directory(root)
        return GeneratedExport(
            file_name=file_name,
            content_type=(
                "text/csv; charset=utf-8"
                if work.format == "csv"
                else "application/json; charset=utf-8"
            ),
            size_bytes=writer.size,
            checksum_sha256=writer.checksum,
            row_count=row_count,
        )
    except BaseException:
        if writer is not None:
            writer.abort()
        _unlink_after_failure(temp_path, artifact_kind="temporary")
        if final_installed:
            _unlink_after_failure(final_path, artifact_kind="installed")
        raise


def _write_csv_header(writer: _BoundedBinaryWriter) -> None:
    _write_csv_row(
        writer,
        (
            "schema_version",
            "scope",
            "subscription_id",
            "search_query_id",
            "provider",
            "trip_type",
            "origin",
            "destination",
            "departure_date",
            "return_date",
            "snapshot_at",
            "observation_id",
            "observed_at",
            "collection_run_id",
            "itinerary_id",
            "fare_offer_id",
            "offer_fingerprint",
            "currency",
            "total_price_minor",
            "is_lowest",
            "is_direct",
        ),
    )


def _write_csv_observation(
    writer: _BoundedBinaryWriter,
    work: ExportWork,
    observation: ExportObservation,
) -> None:
    outbound = work.context.legs[0]
    inbound = work.context.legs[1] if len(work.context.legs) > 1 else None
    _write_csv_row(
        writer,
        (
            "1",
            "canonical_query",
            str(work.subscription_id),
            str(work.search_query_id),
            work.context.provider,
            work.context.trip_type,
            outbound[1],
            outbound[2],
            outbound[3],
            inbound[3] if inbound else "",
            work.snapshot_at.isoformat(),
            str(observation.id),
            observation.observed_at.isoformat(),
            str(observation.collection_run_id),
            str(observation.itinerary_id),
            str(observation.fare_offer_id),
            observation.offer_fingerprint,
            observation.currency,
            observation.total_price_minor,
            observation.is_lowest,
            observation.is_direct,
        ),
    )


def _write_csv_row(writer: _BoundedBinaryWriter, values: tuple[object, ...]) -> None:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerow(
        tuple(sanitize_csv_cell(value) for value in values)
    )
    writer.write_text(buffer.getvalue())


def _write_json_header(writer: _BoundedBinaryWriter, work: ExportWork) -> None:
    metadata = {
        "schema_version": 1,
        "export": {
            "id": str(work.job_id),
            "subscription_id": str(work.subscription_id),
            "search_query_id": str(work.search_query_id),
            "range_start": work.range_start.isoformat(),
            "range_end_exclusive": work.range_end.isoformat(),
            "snapshot_at": work.snapshot_at.isoformat(),
            "scope": "canonical_query",
        },
        "query": {
            "provider": work.context.provider,
            "trip_type": work.context.trip_type,
            "currency": work.context.currency,
            "legs": [
                {
                    "position": leg[0],
                    "origin": leg[1],
                    "destination": leg[2],
                    "departure_date": leg[3],
                }
                for leg in work.context.legs
            ],
        },
    }
    encoded = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    writer.write_text(encoded[:-1] + ',"observations":[')


def _json_observation(observation: ExportObservation) -> dict[str, object]:
    return {
        "collection_run_id": str(observation.collection_run_id),
        "currency": observation.currency,
        "fare_offer_id": str(observation.fare_offer_id),
        "is_direct": observation.is_direct,
        "is_lowest": observation.is_lowest,
        "itinerary_id": str(observation.itinerary_id),
        "observation_id": str(observation.id),
        "observed_at": observation.observed_at.isoformat(),
        "offer_fingerprint": observation.offer_fingerprint,
        "total_price_minor": observation.total_price_minor,
    }


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_after_failure(path: Path, *, artifact_kind: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(
            "export_artifact_cleanup_failed",
            artifact_kind=artifact_kind,
            path=str(path),
            error_type=type(error).__name__,
        )

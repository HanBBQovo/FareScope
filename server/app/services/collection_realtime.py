from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CollectionRun, User, UserSession
from app.models.enums import CollectionStatus, UserStatus
from app.services.collection_visibility import visible_collection_run_condition
from app.settings import Settings

logger = logging.getLogger(__name__)

COLLECTION_RUN_EVENT = "collection-run"
COLLECTION_SNAPSHOT_EVENT = "collection-snapshot"
COLLECTION_CHECKPOINT_EVENT = "collection-checkpoint"
REALTIME_DEGRADED_EVENT = "realtime-degraded"
REALTIME_RECONNECT_EVENT = "realtime-reconnect"
_STREAM_VERSION = "1"
_CURSOR_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
_COLLECTION_STATUSES = frozenset(item.value for item in CollectionStatus)

DisconnectCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class CollectionRunEvent:
    run_id: UUID
    query_id: UUID
    status: str
    updated_at: datetime
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempt: int
    max_attempts: int
    error_code: str | None

    @classmethod
    def from_model(cls, run: CollectionRun) -> CollectionRunEvent:
        return cls(
            run_id=run.id,
            query_id=run.search_query_id,
            status=run.status,
            updated_at=_ensure_utc(run.updated_at),
            scheduled_at=_ensure_utc(run.scheduled_at),
            started_at=_optional_utc(run.started_at),
            finished_at=_optional_utc(run.finished_at),
            attempt=run.attempt,
            max_attempts=run.max_attempts,
            error_code=run.error_code,
        )

    def stream_fields(self) -> dict[str, str]:
        fields = {
            "version": _STREAM_VERSION,
            "run_id": str(self.run_id),
            "query_id": str(self.query_id),
            "status": self.status,
            "updated_at": self.updated_at.isoformat(),
        }
        if self.error_code:
            fields["error_code"] = self.error_code[:120]
        return fields

    def public_payload(self) -> dict[str, object]:
        return {
            "runId": str(self.run_id),
            "status": self.status,
            "updatedAt": self.updated_at.isoformat(),
            "scheduledAt": self.scheduled_at.isoformat(),
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "attempt": self.attempt,
            "maxAttempts": self.max_attempts,
            "errorCode": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class StreamRecord:
    event_id: str
    run_id: UUID | None


class RedisCollectionEventStore:
    def __init__(
        self,
        client: Redis,
        *,
        stream_key: str,
        max_length: int,
    ) -> None:
        self._client = client
        self._stream_key = stream_key
        self._max_length = max_length

    async def publish(self, event: CollectionRunEvent) -> str:
        result = await self._client.xadd(
            self._stream_key,
            event.stream_fields(),
            maxlen=self._max_length,
            approximate=True,
        )
        return _text(result)

    async def current_cursor(self) -> str:
        rows = await self._client.xrevrange(self._stream_key, count=1)
        return _text(rows[0][0]) if rows else "0-0"

    async def read_after(
        self,
        cursor: str,
        *,
        block_ms: int,
        count: int,
    ) -> tuple[StreamRecord, ...]:
        response = await self._client.xread(
            {self._stream_key: cursor},
            block=block_ms,
            count=count,
        )
        records: list[StreamRecord] = []
        for _stream, entries in response:
            for event_id, raw_fields in entries:
                fields = {_text(key): _text(value) for key, value in raw_fields.items()}
                run_id = _parse_stream_run_id(fields)
                records.append(StreamRecord(event_id=_text(event_id), run_id=run_id))
        return tuple(records)


def validate_realtime_cursor(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    if not _CURSOR_PATTERN.fullmatch(candidate):
        raise ValueError("invalid realtime event cursor")
    return candidate


async def load_initial_collection_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    user_session_id: UUID,
    limit: int,
    now: datetime | None = None,
) -> tuple[CollectionRunEvent, ...]:
    current_time = now or datetime.now(UTC)
    owner_scope = visible_collection_run_condition(user_id)
    active_session = exists(
        select(UserSession.id)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.id == user_session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > current_time,
            User.status == UserStatus.ACTIVE.value,
        )
    )
    async with session_factory() as session:
        runs = (
            await session.scalars(
                select(CollectionRun)
                .where(owner_scope, active_session)
                .order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc())
                .limit(limit)
            )
        ).all()
        return tuple(CollectionRunEvent.from_model(run) for run in runs)


async def load_visible_collection_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    user_session_id: UUID,
    run_id: UUID,
    now: datetime | None = None,
) -> CollectionRunEvent | None:
    current_time = now or datetime.now(UTC)
    owner_scope = visible_collection_run_condition(user_id)
    active_session = exists(
        select(UserSession.id)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.id == user_session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > current_time,
            User.status == UserStatus.ACTIVE.value,
        )
    )
    async with session_factory() as session:
        run = await session.scalar(
            select(CollectionRun).where(
                CollectionRun.id == run_id,
                owner_scope,
                active_session,
            )
        )
        return CollectionRunEvent.from_model(run) if run is not None else None


async def realtime_session_is_active(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    user_session_id: UUID,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(UTC)
    async with session_factory() as session:
        return bool(
            await session.scalar(
                select(UserSession.id)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.id == user_session_id,
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > current_time,
                    User.status == UserStatus.ACTIVE.value,
                )
                .limit(1)
            )
        )


async def publish_collection_run_state_safely(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    settings: Settings,
) -> bool:
    """Publish committed state without retaining a DB connection during Redis I/O."""

    return bool(
        await publish_collection_run_states_safely(
            session_factory,
            run_ids=(run_id,),
            settings=settings,
        )
    )


async def publish_collection_run_states_safely(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_ids: tuple[UUID, ...],
    settings: Settings,
) -> int:
    """Publish a committed run batch with one bounded DB read and Redis pipeline."""

    unique_run_ids = tuple(dict.fromkeys(run_ids))
    if not unique_run_ids:
        return 0

    client: Redis | None = None
    try:
        async with session_factory() as session:
            runs = (
                await session.scalars(
                    select(CollectionRun).where(CollectionRun.id.in_(unique_run_ids))
                )
            ).all()
            events = tuple(CollectionRunEvent.from_model(run) for run in runs)
        if not events:
            return 0

        client = create_realtime_redis_client(settings, reader=False)
        pipeline = client.pipeline(transaction=False)
        for event in events:
            pipeline.xadd(
                settings.collection_realtime_stream_key,
                event.stream_fields(),
                maxlen=settings.collection_realtime_stream_max_length,
                approximate=True,
            )
        await asyncio.wait_for(
            pipeline.execute(),
            timeout=settings.collection_realtime_redis_timeout_seconds,
        )
        return len(events)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - realtime cannot roll back committed business state
        logger.warning(
            "collection realtime publish failed",
            extra={
                "run_count": len(unique_run_ids),
                "error_type": type(exc).__name__,
            },
        )
        return 0
    finally:
        if client is not None:
            try:
                await client.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - close failure cannot change business state
                logger.warning(
                    "collection realtime publisher close failed",
                    extra={"error_type": type(exc).__name__},
                )


async def collection_run_event_stream(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    user_session_id: UUID,
    settings: Settings,
    resume_cursor: str | None,
    disconnected: DisconnectCheck | None = None,
    redis_client: Redis | None = None,
) -> AsyncIterator[str]:
    """Yield an owner-filtered SSE stream backed by Redis Streams and PostgreSQL truth."""

    owned_client = redis_client is None
    client = redis_client or create_realtime_redis_client(settings, reader=True)
    store = RedisCollectionEventStore(
        client,
        stream_key=settings.collection_realtime_stream_key,
        max_length=settings.collection_realtime_stream_max_length,
    )
    redis_available = True
    tail_cursor = "0-0"
    try:
        try:
            tail_cursor = await store.current_cursor()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - snapshot remains useful during Redis outages
            redis_available = False
            _log_stream_degradation("redis_unavailable", exc, user_id=user_id)

        try:
            active = await realtime_session_is_active(
                session_factory,
                user_id=user_id,
                user_session_id=user_session_id,
            )
            if not active:
                yield _degraded_frame("session_expired", settings)
                return
            snapshot = await load_initial_collection_snapshot(
                session_factory,
                user_id=user_id,
                user_session_id=user_session_id,
                limit=settings.collection_realtime_snapshot_limit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - response has already entered streaming mode
            _log_stream_degradation("database_unavailable", exc, user_id=user_id)
            yield _degraded_frame("database_unavailable", settings)
            return

        stream_cursor = resume_cursor or tail_cursor
        yield encode_sse(
            COLLECTION_SNAPSHOT_EVENT,
            {
                "cursor": stream_cursor,
                "items": [item.public_payload() for item in snapshot],
                "generatedAt": datetime.now(UTC).isoformat(),
            },
            event_id=stream_cursor if redis_available else None,
            retry_ms=settings.collection_realtime_retry_ms,
        )
        if not redis_available:
            yield _degraded_frame("redis_unavailable", settings)
            return

        cursor = stream_cursor
        started_at = time.monotonic()
        while time.monotonic() - started_at < settings.collection_realtime_connection_seconds:
            if disconnected is not None and await disconnected():
                return
            remaining_ms = int(
                max(
                    1_000,
                    min(
                        settings.collection_realtime_block_ms,
                        (
                            settings.collection_realtime_connection_seconds
                            - (time.monotonic() - started_at)
                        )
                        * 1_000,
                    ),
                )
            )
            try:
                records = await store.read_after(
                    cursor,
                    block_ms=remaining_ms,
                    count=settings.collection_realtime_read_count,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - emit a safe degradation then use polling
                _log_stream_degradation("redis_unavailable", exc, user_id=user_id)
                yield _degraded_frame("redis_unavailable", settings)
                return

            if not records:
                try:
                    active = await realtime_session_is_active(
                        session_factory,
                        user_id=user_id,
                        user_session_id=user_session_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - do not retain an unauditable stream
                    _log_stream_degradation("database_unavailable", exc, user_id=user_id)
                    yield _degraded_frame("database_unavailable", settings)
                    return
                if not active:
                    yield _degraded_frame("session_expired", settings)
                    return
                yield ": heartbeat\n\n"
                continue

            try:
                active = await realtime_session_is_active(
                    session_factory,
                    user_id=user_id,
                    user_session_id=user_session_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - session validity cannot be guessed
                _log_stream_degradation("database_unavailable", exc, user_id=user_id)
                yield _degraded_frame("database_unavailable", settings)
                return
            if not active:
                yield _degraded_frame("session_expired", settings)
                return

            for record in records:
                cursor = record.event_id
                if record.run_id is None:
                    logger.warning(
                        "discarded malformed collection realtime event",
                        extra={"event_id": record.event_id},
                    )
                    continue
                try:
                    event = await load_visible_collection_event(
                        session_factory,
                        user_id=user_id,
                        user_session_id=user_session_id,
                        run_id=record.run_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - ownership cannot be guessed on DB failure
                    _log_stream_degradation("database_unavailable", exc, user_id=user_id)
                    yield _degraded_frame("database_unavailable", settings)
                    return
                if event is None:
                    continue
                yield encode_sse(
                    COLLECTION_RUN_EVENT,
                    event.public_payload(),
                    event_id=record.event_id,
                )

            # Every valid record above has passed a fresh database session/owner check.
            # A data-free checkpoint lets sparse subscribers acknowledge global records
            # that were not visible to them without weakening that authorization fence.
            yield encode_sse(
                COLLECTION_CHECKPOINT_EVENT,
                {"cursor": cursor},
                event_id=cursor,
            )

        yield encode_sse(
            REALTIME_RECONNECT_EVENT,
            {
                "reason": "connection_timeout",
                "at": datetime.now(UTC).isoformat(),
            },
            retry_ms=settings.collection_realtime_retry_ms,
        )
    finally:
        if owned_client:
            try:
                await client.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - connection is already leaving service
                logger.warning(
                    "collection realtime reader close failed",
                    extra={"error_type": type(exc).__name__, "user_id": str(user_id)},
                )


def create_realtime_redis_client(settings: Settings, *, reader: bool) -> Redis:
    socket_timeout = settings.collection_realtime_redis_timeout_seconds
    if reader:
        socket_timeout += settings.collection_realtime_block_ms / 1_000
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.collection_realtime_redis_timeout_seconds,
        socket_timeout=socket_timeout,
        health_check_interval=30,
    )


def encode_sse(
    event: str,
    data: Mapping[str, Any],
    *,
    event_id: str | None = None,
    retry_ms: int | None = None,
) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    lines.append(f"event: {event}")
    lines.append(
        "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    )
    return "\n".join(lines) + "\n\n"


def _degraded_frame(reason: str, settings: Settings) -> str:
    return encode_sse(
        REALTIME_DEGRADED_EVENT,
        {
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
            "pollAfterMs": max(settings.collection_realtime_retry_ms, 5_000),
        },
        retry_ms=settings.collection_realtime_retry_ms,
    )


def _parse_stream_run_id(fields: Mapping[str, str]) -> UUID | None:
    if fields.get("version") != _STREAM_VERSION:
        return None
    if fields.get("status") not in _COLLECTION_STATUSES:
        return None
    try:
        UUID(fields["query_id"])
        return UUID(fields["run_id"])
    except (KeyError, ValueError):
        return None


def _log_stream_degradation(reason: str, exc: Exception, *, user_id: UUID) -> None:
    logger.warning(
        "collection realtime stream degraded",
        extra={
            "reason": reason,
            "error_type": type(exc).__name__,
            "user_id": str(user_id),
        },
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _ensure_utc(value) if value is not None else None

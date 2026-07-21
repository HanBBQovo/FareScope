"""Operational collection metrics without exposing provider payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectionRun, Provider, SchemaObservation, SearchQuery, Subscription
from app.models.enums import CollectionStatus
from app.services.collection_visibility import visible_collection_run_condition

CELERY_READY_QUEUES = ("collector", "default", "analysis", "notifications")


@dataclass(frozen=True, slots=True)
class RunStatusCounts:
    ready: int
    retrying: int
    leased: int
    running: int
    failed_24h: int


@dataclass(frozen=True, slots=True)
class QueueDepths:
    available: bool
    collector: int | None
    default: int | None
    analysis: int | None
    notifications: int | None


@dataclass(frozen=True, slots=True)
class SchemaSignal:
    provider: str
    endpoint: str
    schema_fingerprint: str
    top_level_fields: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    state: Literal["new", "current", "historical"]


@dataclass(frozen=True, slots=True)
class CollectionOperationsSnapshot:
    generated_at: datetime
    run_counts: RunStatusCounts
    queue_depths: QueueDepths
    schema_signals: tuple[SchemaSignal, ...]


async def load_collection_operations(
    session: AsyncSession,
    *,
    user_id: UUID,
    redis_url: str,
    now: datetime | None = None,
    schema_limit: int = 20,
) -> CollectionOperationsSnapshot:
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    run_counts = await _load_run_status_counts(
        session,
        user_id=user_id,
        now=generated_at,
    )
    schema_signals = await _load_schema_signals(
        session,
        user_id=user_id,
        now=generated_at,
        limit=schema_limit,
    )
    queue_depths = await load_queue_depths(redis_url)
    return CollectionOperationsSnapshot(
        generated_at=generated_at,
        run_counts=run_counts,
        queue_depths=queue_depths,
        schema_signals=schema_signals,
    )


async def _load_run_status_counts(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
) -> RunStatusCounts:
    failed_since = now - timedelta(hours=24)
    statement = select(
        func.count()
        .filter(
            and_(
                CollectionRun.status == CollectionStatus.PENDING.value,
                CollectionRun.scheduled_at <= now,
            )
        )
        .label("ready"),
        func.count()
        .filter(
            and_(
                CollectionRun.status == CollectionStatus.PENDING.value,
                CollectionRun.scheduled_at > now,
            )
        )
        .label("retrying"),
        func.count().filter(CollectionRun.status == CollectionStatus.LEASED.value).label("leased"),
        func.count()
        .filter(CollectionRun.status == CollectionStatus.RUNNING.value)
        .label("running"),
        func.count()
        .filter(
            and_(
                CollectionRun.status == CollectionStatus.FAILED.value,
                CollectionRun.finished_at >= failed_since,
            )
        )
        .label("failed_24h"),
    ).where(visible_collection_run_condition(user_id))
    row = (await session.execute(statement)).one()
    return RunStatusCounts(
        ready=int(row.ready or 0),
        retrying=int(row.retrying or 0),
        leased=int(row.leased or 0),
        running=int(row.running or 0),
        failed_24h=int(row.failed_24h or 0),
    )


async def _load_schema_signals(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
    limit: int,
) -> tuple[SchemaSignal, ...]:
    bounded_limit = min(max(limit, 1), 100)
    provider_codes = (
        select(SearchQuery.provider)
        .join(Subscription, Subscription.search_query_id == SearchQuery.id)
        .where(Subscription.user_id == user_id)
        .distinct()
    )
    statement = (
        select(SchemaObservation, Provider.code)
        .join(Provider, Provider.id == SchemaObservation.provider_id)
        .where(Provider.code.in_(provider_codes))
        .order_by(
            SchemaObservation.last_seen_at.desc(),
            SchemaObservation.id.desc(),
        )
        .limit(bounded_limit)
    )
    rows = (await session.execute(statement)).all()
    current_endpoints: set[tuple[str, str]] = set()
    new_since = now - timedelta(hours=24)
    signals: list[SchemaSignal] = []
    for observation, provider_code in rows:
        endpoint_key = (provider_code, observation.endpoint)
        is_current = endpoint_key not in current_endpoints
        current_endpoints.add(endpoint_key)
        if is_current and observation.first_seen_at >= new_since:
            state: Literal["new", "current", "historical"] = "new"
        elif is_current:
            state = "current"
        else:
            state = "historical"
        signals.append(
            SchemaSignal(
                provider=provider_code,
                endpoint=observation.endpoint,
                schema_fingerprint=observation.schema_fingerprint,
                top_level_fields=extract_top_level_fields(observation.field_summary),
                first_seen_at=observation.first_seen_at,
                last_seen_at=observation.last_seen_at,
                occurrence_count=observation.occurrence_count,
                state=state,
            )
        )
    return tuple(signals)


async def load_queue_depths(redis_url: str) -> QueueDepths:
    client = Redis.from_url(
        redis_url,
        decode_responses=False,
        socket_connect_timeout=0.75,
        socket_timeout=0.75,
        health_check_interval=30,
    )
    try:
        pipeline = client.pipeline(transaction=False)
        for queue in CELERY_READY_QUEUES:
            pipeline.llen(queue)
        values = await pipeline.execute()
        depths = {
            queue: max(0, int(value))
            for queue, value in zip(CELERY_READY_QUEUES, values, strict=True)
        }
        return QueueDepths(available=True, **depths)
    except (RedisError, TimeoutError, OSError, ValueError, TypeError):
        return QueueDepths(
            available=False,
            collector=None,
            default=None,
            analysis=None,
            notifications=None,
        )
    finally:
        await client.aclose()


def extract_top_level_fields(summary: object) -> tuple[str, ...]:
    if not isinstance(summary, dict):
        return ()
    shape = summary.get("shape")
    candidate = shape if isinstance(shape, dict) else summary.get("top_level")
    if not isinstance(candidate, dict):
        return ()
    return tuple(sorted(str(key)[:120] for key in candidate)[:40])

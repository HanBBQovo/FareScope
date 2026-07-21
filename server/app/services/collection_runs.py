from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectionRun, Provider, SearchQuery
from app.models.enums import CollectionStatus


async def ensure_on_demand_collection_run(
    session: AsyncSession,
    *,
    search_query: SearchQuery,
    now: datetime | None = None,
) -> CollectionRun:
    current_time = now or datetime.now(UTC)
    bucket = int(current_time.timestamp() // 900)
    idempotency_key = f"on-demand:{search_query.query_hash}:{bucket}"

    active = await session.scalar(
        select(CollectionRun)
        .where(
            CollectionRun.search_query_id == search_query.id,
            CollectionRun.status.in_(
                (
                    CollectionStatus.PENDING.value,
                    CollectionStatus.LEASED.value,
                    CollectionStatus.RUNNING.value,
                )
            ),
        )
        .order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )
    if active is not None:
        return active

    provider = await session.scalar(
        select(Provider).where(Provider.code == search_query.provider, Provider.enabled.is_(True))
    )
    if provider is None:
        raise RuntimeError(f"provider is not enabled: {search_query.provider}")

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
            scheduled_at=current_time,
            run_metadata={"trigger": "on_demand", "bucket_seconds": 900},
        )
        .on_conflict_do_nothing()
        .returning(CollectionRun.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    await session.flush()
    if inserted_id is not None:
        run = await session.get(CollectionRun, inserted_id)
        if run is None:
            raise RuntimeError("created collection run could not be reloaded")
        return run

    existing = await session.scalar(
        select(CollectionRun)
        .where(
            (
                CollectionRun.idempotency_key == idempotency_key
            )
            | (
                (CollectionRun.search_query_id == search_query.id)
                & CollectionRun.status.in_(
                    (
                        CollectionStatus.PENDING.value,
                        CollectionStatus.LEASED.value,
                        CollectionStatus.RUNNING.value,
                    )
                )
            )
        )
        .order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )
    if existing is None:
        raise RuntimeError("collection run conflict resolved without a visible row")
    return existing

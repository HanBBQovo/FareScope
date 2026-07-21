from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectionRun, Provider, SearchQuery
from app.models.enums import CollectionStatus


async def ensure_on_demand_collection_run(
    session: AsyncSession,
    *,
    search_query: SearchQuery,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> CollectionRun:
    current_time = now or datetime.now(UTC)
    bucket = int(current_time.timestamp() // 900)
    bucket_start = datetime.fromtimestamp(bucket * 900, UTC)

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
        _attach_on_demand_user(active, user_id)
        await session.flush()
        return active

    recent_success = await session.scalar(
        select(CollectionRun)
        .where(
            CollectionRun.search_query_id == search_query.id,
            CollectionRun.status == CollectionStatus.SUCCEEDED.value,
            CollectionRun.scheduled_at >= bucket_start,
            CollectionRun.finished_at.is_not(None),
        )
        .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )
    if recent_success is not None:
        _attach_on_demand_user(recent_success, user_id)
        await session.flush()
        return recent_success

    terminal_generation = await session.scalar(
        select(func.count(CollectionRun.id)).where(
            CollectionRun.search_query_id == search_query.id,
            CollectionRun.status.in_(
                (
                    CollectionStatus.FAILED.value,
                    CollectionStatus.CANCELED.value,
                )
            ),
            CollectionRun.scheduled_at >= bucket_start,
        )
    )
    idempotency_key = (
        f"on-demand:{search_query.query_hash}:{bucket}:{int(terminal_generation or 0)}"
    )

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
            run_metadata=_on_demand_metadata(user_id),
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
        _attach_on_demand_user(run, user_id)
        await session.flush()
        return run

    existing = await session.scalar(
        select(CollectionRun)
        .where(
            (CollectionRun.search_query_id == search_query.id)
            & CollectionRun.status.in_(
                (
                    CollectionStatus.PENDING.value,
                    CollectionStatus.LEASED.value,
                    CollectionStatus.RUNNING.value,
                )
            )
        )
        .order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )
    if existing is None:
        raise RuntimeError("collection run conflict resolved without a visible row")
    _attach_on_demand_user(existing, user_id)
    await session.flush()
    return existing


def _on_demand_metadata(user_id: UUID | None) -> dict[str, object]:
    metadata: dict[str, object] = {"trigger": "on_demand", "bucket_seconds": 900}
    if user_id is not None:
        metadata["on_demand_user_ids"] = [str(user_id)]
    return metadata


def _attach_on_demand_user(run: CollectionRun, user_id: UUID | None) -> None:
    if user_id is None:
        return
    metadata = dict(run.run_metadata or {})
    existing = metadata.get("on_demand_user_ids")
    values = [str(item) for item in existing] if isinstance(existing, list) else []
    user_id_text = str(user_id)
    if user_id_text not in values:
        values.append(user_id_text)
    metadata["on_demand_user_ids"] = values[-100:]
    run.run_metadata = metadata

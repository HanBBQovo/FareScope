from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import (
    CollectionRun,
    Provider,
    SchemaObservation,
    SearchQuery,
    Subscription,
    User,
)
from app.models.enums import CollectionStatus
from app.services import collection_operations
from app.services.collection_operations import QueueDepths, load_collection_operations

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_operations_counts_are_owner_scoped_and_schema_summary_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)

    async def fake_queue_depths(_redis_url: str) -> QueueDepths:
        return QueueDepths(
            available=True,
            collector=4,
            default=1,
            analysis=2,
            notifications=3,
        )

    monkeypatch.setattr(collection_operations, "load_queue_depths", fake_queue_depths)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            provider = await session.scalar(select(Provider).where(Provider.code == "ctrip"))
            assert provider is not None
            token = uuid4().hex
            owner = _user(f"ops-owner-{token}")
            other = _user(f"ops-other-{token}")
            owner_query = _query(f"{token}a")
            other_query = _query(f"{token}b")
            session.add_all([owner, other, owner_query, other_query])
            await session.flush()
            session.add_all(
                [
                    _subscription(owner.id, owner_query.id, now),
                    _subscription(other.id, other_query.id, now),
                ]
            )
            await session.flush()

            owner_runs = [
                _run(
                    provider.id,
                    owner_query.id,
                    token,
                    "ready",
                    CollectionStatus.PENDING.value,
                    now - timedelta(seconds=1),
                ),
                _run(
                    provider.id,
                    owner_query.id,
                    token,
                    "retry",
                    CollectionStatus.PENDING.value,
                    now + timedelta(minutes=5),
                ),
                _run(
                    provider.id, owner_query.id, token, "leased", CollectionStatus.LEASED.value, now
                ),
                _run(
                    provider.id,
                    owner_query.id,
                    token,
                    "running",
                    CollectionStatus.RUNNING.value,
                    now,
                ),
                _run(
                    provider.id,
                    owner_query.id,
                    token,
                    CollectionStatus.FAILED.value,
                    "failed",
                    now,
                    finished_at=now - timedelta(hours=1),
                ),
            ]
            other_runs = [
                _run(
                    provider.id,
                    other_query.id,
                    token,
                    f"other-{index}",
                    CollectionStatus.RUNNING.value,
                    now,
                )
                for index in range(3)
            ]
            session.add_all(owner_runs + other_runs)
            await session.flush()
            endpoint = f"/perf-safe-shape/{token}"
            session.add(
                SchemaObservation(
                    provider_id=provider.id,
                    collection_run_id=owner_runs[0].id,
                    endpoint=endpoint,
                    schema_fingerprint=(token * 2)[:64],
                    field_summary={
                        "shape": {
                            "data": {"secret_value_is_not_stored": "str"},
                            "status": "str",
                        }
                    },
                    first_seen_at=now,
                    last_seen_at=now,
                    occurrence_count=1,
                )
            )
            await session.flush()

            snapshot = await load_collection_operations(
                session,
                user_id=owner.id,
                redis_url="redis://example.invalid/0",
                now=now,
                schema_limit=100,
            )

            assert snapshot.run_counts.ready == 1
            assert snapshot.run_counts.retrying == 1
            assert snapshot.run_counts.leased == 1
            assert snapshot.run_counts.running == 1
            assert snapshot.run_counts.failed_24h == 1
            assert snapshot.queue_depths.collector == 4
            signal = next(item for item in snapshot.schema_signals if item.endpoint == endpoint)
            assert signal.top_level_fields == ("data", "status")
            assert signal.state == "new"
        finally:
            await session.close()
            await transaction.rollback()
            await engine.dispose()


def _user(username: str) -> User:
    return User(
        username=username,
        normalized_username=username,
        display_name=username,
        role="member",
        status="active",
    )


def _query(seed: str) -> SearchQuery:
    return SearchQuery(
        provider="ctrip",
        query_hash=(seed * 3)[:64],
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="CNY",
        direct_only=False,
        normalized_query={"route": seed},
    )


def _subscription(user_id, query_id, now: datetime) -> Subscription:
    return Subscription(
        user_id=user_id,
        search_query_id=query_id,
        name="Operations route",
        enabled=True,
        poll_interval_seconds=1800,
        next_due_at=now,
        tags=[],
    )


def _run(
    provider_id,
    query_id,
    token: str,
    suffix: str,
    status: str,
    scheduled_at: datetime,
    *,
    finished_at: datetime | None = None,
) -> CollectionRun:
    return CollectionRun(
        search_query_id=query_id,
        provider_id=provider_id,
        idempotency_key=f"ops:{token}:{suffix}",
        status=status,
        attempt=1,
        max_attempts=3,
        scheduled_at=scheduled_at,
        finished_at=finished_at,
        run_metadata={},
    )

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import CollectionRun, Provider, SearchQuery, Subscription, User, UserSession
from app.services.collection_realtime import (
    COLLECTION_CHECKPOINT_EVENT,
    COLLECTION_RUN_EVENT,
    COLLECTION_SNAPSHOT_EVENT,
    collection_run_event_stream,
    publish_collection_run_state_safely,
)
from app.settings import Settings

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")
REDIS_URL = os.getenv("FARESCOPE_TEST_REDIS_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.redis,
    pytest.mark.skipif(
        DATABASE_URL is None or REDIS_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL and FARESCOPE_TEST_REDIS_URL are required",
    ),
]


async def test_event_committed_during_snapshot_window_is_read_from_saved_tail_cursor() -> None:
    assert DATABASE_URL is not None
    assert REDIS_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    cleanup = Redis.from_url(REDIS_URL)
    stream_key = f"farescope:test:realtime:e2e:{uuid4().hex}"
    now = datetime.now(UTC).replace(microsecond=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        stream = None
        try:
            async with factory() as session, session.begin():
                provider = await session.scalar(select(Provider).where(Provider.code == "ctrip"))
                assert provider is not None
                token = uuid4().hex
                user = User(
                    username=f"rt-e2e-{token}",
                    normalized_username=f"rt-e2e-{token}",
                    display_name=f"rt-e2e-{token}",
                    role="member",
                    status="active",
                )
                query = SearchQuery(
                    provider="ctrip",
                    query_hash=token + uuid4().hex,
                    trip_type="one_way",
                    adults=1,
                    children=0,
                    infants=0,
                    cabin="economy",
                    currency="CNY",
                    direct_only=False,
                    normalized_query={"realtime_e2e": True},
                )
                hidden_query = SearchQuery(
                    provider="ctrip",
                    query_hash=uuid4().hex + uuid4().hex,
                    trip_type="one_way",
                    adults=1,
                    children=0,
                    infants=0,
                    cabin="economy",
                    currency="CNY",
                    direct_only=False,
                    normalized_query={"realtime_e2e_hidden": True},
                )
                session.add_all([user, query, hidden_query])
                await session.flush()
                login = UserSession(
                    user_id=user.id,
                    token_hash=(token * 4)[:128],
                    expires_at=now + timedelta(hours=1),
                )
                session.add_all(
                    [
                        login,
                        Subscription(
                            user_id=user.id,
                            search_query_id=query.id,
                            name="Realtime race window",
                            enabled=True,
                            poll_interval_seconds=900,
                            next_due_at=now,
                            tags=[],
                        ),
                    ]
                )
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"realtime-e2e:{token}",
                    status="running",
                    attempt=1,
                    max_attempts=3,
                    scheduled_at=now,
                    started_at=now,
                    run_metadata={},
                )
                hidden_run = CollectionRun(
                    search_query_id=hidden_query.id,
                    provider_id=provider.id,
                    idempotency_key=f"realtime-e2e-hidden:{token}",
                    status="running",
                    attempt=1,
                    max_attempts=3,
                    scheduled_at=now,
                    started_at=now,
                    run_metadata={},
                )
                session.add_all([run, hidden_run])

            settings = Settings(
                _env_file=None,
                redis_url=REDIS_URL,
                collection_realtime_stream_key=stream_key,
                collection_realtime_block_ms=1_000,
                collection_realtime_connection_seconds=30,
                collection_realtime_redis_timeout_seconds=1,
            )
            stream = collection_run_event_stream(
                factory,
                user_id=user.id,
                user_session_id=login.id,
                settings=settings,
                resume_cursor=None,
            )
            snapshot = await anext(stream)
            assert f"event: {COLLECTION_SNAPSHOT_EVENT}" in snapshot
            assert str(run.id) in snapshot
            assert str(hidden_run.id) not in snapshot
            assert '"status":"running"' in snapshot

            assert (
                await publish_collection_run_state_safely(
                    factory,
                    run_id=hidden_run.id,
                    settings=settings,
                )
                is True
            )
            checkpoint = await anext(stream)
            assert f"event: {COLLECTION_CHECKPOINT_EVENT}" in checkpoint
            assert str(hidden_run.id) not in checkpoint
            assert "query_id" not in checkpoint

            async with factory() as session, session.begin():
                persisted = await session.get(CollectionRun, run.id)
                assert persisted is not None
                persisted.status = "succeeded"
                persisted.finished_at = datetime.now(UTC)
            assert (
                await publish_collection_run_state_safely(
                    factory,
                    run_id=run.id,
                    settings=settings,
                )
                is True
            )

            update = await anext(stream)
            assert f"event: {COLLECTION_RUN_EVENT}" in update
            assert str(run.id) in update
            assert '"status":"succeeded"' in update
            assert update.startswith("id: ")
        finally:
            if stream is not None:
                await stream.aclose()
            await cleanup.delete(stream_key)
            await cleanup.aclose()
            await transaction.rollback()
    await engine.dispose()

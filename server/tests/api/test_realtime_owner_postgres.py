from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import CollectionRun, Provider, SearchQuery, Subscription, User, UserSession
from app.services.collection_realtime import (
    load_initial_collection_snapshot,
    load_visible_collection_event,
    realtime_session_is_active,
)

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_shared_run_is_visible_only_to_current_canonical_query_subscribers() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                provider = await session.get(Provider, await _provider_id(session))
                assert provider is not None
                token = uuid4().hex
                query = _query(f"{token}a")
                other_query = _query(f"{token}b")
                owner = _user(f"rt-owner-{token}")
                co_subscriber = _user(f"rt-shared-{token}")
                outsider = _user(f"rt-outsider-{token}")
                session.add_all([query, other_query, owner, co_subscriber, outsider])
                await session.flush()
                owner_session = _session(owner.id, token + "a", now)
                shared_session = _session(co_subscriber.id, token + "b", now)
                outsider_session = _session(outsider.id, token + "c", now)
                owner_subscription = _subscription(owner.id, query.id, now)
                session.add_all(
                    [
                        owner_session,
                        shared_session,
                        outsider_session,
                        owner_subscription,
                        _subscription(co_subscriber.id, query.id, now),
                        _subscription(outsider.id, other_query.id, now),
                    ]
                )
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"realtime-owner:{token}",
                    status="running",
                    attempt=1,
                    max_attempts=3,
                    scheduled_at=now,
                    started_at=now,
                    run_metadata={},
                )
                session.add(run)

            assert (
                await load_visible_collection_event(
                    factory,
                    user_id=owner.id,
                    user_session_id=owner_session.id,
                    run_id=run.id,
                )
                is not None
            )
            assert (
                await load_visible_collection_event(
                    factory,
                    user_id=co_subscriber.id,
                    user_session_id=shared_session.id,
                    run_id=run.id,
                )
                is not None
            )
            assert (
                await load_visible_collection_event(
                    factory,
                    user_id=outsider.id,
                    user_session_id=outsider_session.id,
                    run_id=run.id,
                )
                is None
            )
            assert {
                item.run_id
                for item in await load_initial_collection_snapshot(
                    factory,
                    user_id=owner.id,
                    user_session_id=owner_session.id,
                    limit=100,
                )
            } == {run.id}

            async with factory() as session, session.begin():
                await session.delete(await session.get(Subscription, owner_subscription.id))
            assert (
                await load_visible_collection_event(
                    factory,
                    user_id=owner.id,
                    user_session_id=owner_session.id,
                    run_id=run.id,
                )
                is None
            )

            async with factory() as session, session.begin():
                persisted_session = await session.get(UserSession, shared_session.id)
                assert persisted_session is not None
                persisted_session.revoked_at = now
            assert (
                await realtime_session_is_active(
                    factory,
                    user_id=co_subscriber.id,
                    user_session_id=shared_session.id,
                    now=now + timedelta(seconds=1),
                )
                is False
            )
            assert (
                await load_visible_collection_event(
                    factory,
                    user_id=co_subscriber.id,
                    user_session_id=shared_session.id,
                    run_id=run.id,
                    now=now + timedelta(seconds=1),
                )
                is None
            )
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _provider_id(session) -> object:
    from sqlalchemy import select

    return await session.scalar(select(Provider.id).where(Provider.code == "ctrip"))


def _query(query_hash: str) -> SearchQuery:
    return SearchQuery(
        provider="ctrip",
        query_hash=(query_hash * 2)[:64],
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="CNY",
        direct_only=False,
        normalized_query={"realtime_test": query_hash},
    )


def _user(username: str) -> User:
    return User(
        username=username,
        normalized_username=username,
        display_name=username,
        role="member",
        status="active",
    )


def _session(user_id, token: str, now: datetime) -> UserSession:
    return UserSession(
        user_id=user_id,
        token_hash=(token * 4)[:128],
        expires_at=now + timedelta(hours=1),
    )


def _subscription(user_id, query_id, now: datetime) -> Subscription:
    return Subscription(
        user_id=user_id,
        search_query_id=query_id,
        name="Realtime owner contract",
        enabled=True,
        poll_interval_seconds=900,
        next_due_at=now,
        tags=[],
    )

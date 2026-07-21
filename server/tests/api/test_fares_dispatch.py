from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.routes.fares as fares_routes
from app.main import create_app
from app.models import CollectionRun, SearchQuery, User, UserSession
from app.security import token_digest
from app.services.collection_dispatch import PublishResult
from app.settings import get_settings

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_fare_search_stays_available_when_broker_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    settings = get_settings()
    raw_session_token = f"test-session-{uuid4().hex}"
    user_id: UUID | None = None
    run_id: UUID | None = None
    query_id: UUID | None = None
    dispatched_run_ids: list[UUID] = []

    async def broker_unavailable(
        _session_factory: object,
        *,
        run_id: UUID,
        lease_seconds: int,
        **_kwargs: object,
    ) -> PublishResult:
        assert lease_seconds == settings.collection_dispatch_lease_seconds
        dispatched_run_ids.append(run_id)
        return PublishResult(
            run_id=run_id,
            enqueued=False,
            error_type="OperationalError",
        )

    monkeypatch.setattr(
        fares_routes,
        "dispatch_collection_run_safely",
        broker_unavailable,
    )

    try:
        async with factory() as session, session.begin():
            email = f"fare-dispatch-{uuid4().hex}@example.test"
            username = email.split("@", 1)[0]
            user = User(
                username=username,
                normalized_username=username,
                email=email,
                display_name=username,
                role="member",
                status="active",
            )
            session.add(user)
            await session.flush()
            user_id = user.id
            session.add(
                UserSession(
                    user_id=user.id,
                    token_hash=token_digest(raw_session_token),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )

        app = create_app()
        app.state.database_engine = engine
        app.state.session_factory = factory
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={settings.session_cookie_name: raw_session_token},
        ) as client:
            response = await client.get(
                "/api/fares/search",
                params={
                    "origin": "SHA",
                    "destination": "TYO",
                    "departureDate": "2027-08-15",
                    "tripType": "oneway",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        run_id = UUID(payload["collection"]["runId"])
        assert dispatched_run_ids == [run_id]
        assert payload["collection"]["status"] == "pending"
        async with factory() as session:
            run = await session.get(CollectionRun, run_id)
            assert run is not None
            assert run.status == "pending"
            query_id = run.search_query_id
    finally:
        async with factory() as session, session.begin():
            if run_id is not None:
                await session.execute(delete(CollectionRun).where(CollectionRun.id == run_id))
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
            if query_id is not None:
                remaining_runs = await session.scalar(
                    select(CollectionRun.id)
                    .where(CollectionRun.search_query_id == query_id)
                    .limit(1)
                )
                if remaining_runs is None:
                    await session.execute(delete(SearchQuery).where(SearchQuery.id == query_id))
        await engine.dispose()

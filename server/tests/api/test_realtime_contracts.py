from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import CurrentIdentity, get_current_identity
from app.api.routes import realtime as realtime_routes
from app.main import create_app
from app.models import User, UserSession
from app.services.collection_realtime import encode_sse
from app.settings import Settings, get_settings


def test_realtime_api_exposes_cursor_and_last_event_id_contract() -> None:
    operation = create_app().openapi()["paths"]["/api/realtime/collection-runs"]["get"]
    parameters = {(item["in"], item["name"]): item for item in operation["parameters"]}

    assert ("query", "cursor") in parameters
    assert ("header", "Last-Event-ID") in parameters
    assert "text/event-stream" in operation["responses"]["200"]["content"]


@pytest.mark.asyncio
async def test_realtime_api_requires_an_authenticated_session_cookie() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/realtime/collection-runs")

    assert response.status_code == 401
    assert response.json() == {"detail": "not authenticated"}


@pytest.mark.asyncio
async def test_authenticated_api_streams_sse_without_proxy_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    identity = CurrentIdentity(
        user=User(
            id=user_id,
            username="realtime-contract",
            normalized_username="realtime-contract",
            display_name="Realtime contract",
            role="member",
            status="active",
        ),
        session=UserSession(
            id=uuid4(),
            user_id=user_id,
            token_hash="contract-token",
            expires_at=datetime.now(UTC),
        ),
    )

    async def finite_stream(*_args: object, **_kwargs: object):
        yield encode_sse(
            "collection-snapshot",
            {"cursor": "0-0", "items": [], "generatedAt": "2026-07-21T00:00:00Z"},
            event_id="0-0",
        )

    monkeypatch.setattr(realtime_routes, "collection_run_event_stream", finite_stream)
    app = create_app()
    app.state.session_factory = object()
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/realtime/collection-runs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "event: collection-snapshot" in response.text

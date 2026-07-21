import httpx
import pytest

from app.api.dependencies import get_database_session
from app.api.routes import health
from app.main import create_app


@pytest.mark.asyncio
async def test_liveness() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "api"
    assert len(response.headers["x-request-id"]) == 32


@pytest.mark.asyncio
async def test_request_id_is_preserved_when_it_is_safe() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/health/live",
            headers={"X-Request-ID": "fare-ui:request-42"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "fare-ui:request-42"


@pytest.mark.asyncio
async def test_unsafe_request_id_is_replaced() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/health/live",
            headers={"X-Request-ID": "unsafe request id"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "unsafe request id"
    assert len(response.headers["x-request-id"]) == 32


class _HealthyDatabase:
    async def execute(self, statement):
        return statement


class _FailedDatabase:
    async def execute(self, statement):
        raise ConnectionError("database unavailable")


@pytest.mark.asyncio
async def test_readiness_checks_postgres_and_redis(monkeypatch) -> None:
    app = create_app()

    async def database_override():
        yield _HealthyDatabase()

    async def redis_ping(_redis_url: str) -> None:
        return None

    app.dependency_overrides[get_database_session] = database_override
    monkeypatch.setattr(health, "_ping_redis", redis_ping)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"] == {"postgres": "ok", "redis": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_a_dependency_is_unavailable(monkeypatch) -> None:
    app = create_app()

    async def database_override():
        yield _FailedDatabase()

    async def redis_ping(_redis_url: str) -> None:
        return None

    app.dependency_overrides[get_database_session] = database_override
    monkeypatch.setattr(health, "_ping_redis", redis_ping)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"] == {"postgres": "error", "redis": "ok"}

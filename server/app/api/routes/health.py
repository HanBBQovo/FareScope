from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.dependencies import DatabaseSession, SettingsDependency

router = APIRouter()


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    service: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    timestamp: datetime
    dependencies: dict[str, Literal["ok", "error"]]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok", service="api", timestamp=datetime.now(UTC))


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ReadinessResponse | JSONResponse:
    dependencies: dict[str, Literal["ok", "error"]] = {
        "postgres": "error",
        "redis": "error",
    }

    try:
        await database.execute(text("SELECT 1"))
    except Exception:  # Dependency errors are reported without leaking connection details.
        pass
    else:
        dependencies["postgres"] = "ok"

    try:
        await _ping_redis(settings.redis_url)
    except Exception:
        pass
    else:
        dependencies["redis"] = "ok"

    ready = all(value == "ok" for value in dependencies.values())
    payload = ReadinessResponse(
        status="ok" if ready else "degraded",
        service="api",
        timestamp=datetime.now(UTC),
        dependencies=dependencies,
    )
    if ready:
        return payload
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
    )


async def _ping_redis(redis_url: str) -> None:
    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        if not await client.ping():
            raise ConnectionError("Redis ping returned false")
    finally:
        await client.aclose()

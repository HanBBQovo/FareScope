from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    service: str
    timestamp: datetime


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok", service="api", timestamp=datetime.now(UTC))

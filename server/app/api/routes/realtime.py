from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import IdentityDependency, SettingsDependency
from app.services.collection_realtime import (
    collection_run_event_stream,
    validate_realtime_cursor,
)

router = APIRouter()


@router.get(
    "/collection-runs",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Owner-scoped collection state event stream",
        }
    },
)
async def collection_run_events(
    request: Request,
    identity: IdentityDependency,
    settings: SettingsDependency,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=64)] = None,
) -> StreamingResponse:
    try:
        resume_cursor = validate_realtime_cursor(last_event_id or cursor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    events = collection_run_event_stream(
        request.app.state.session_factory,
        user_id=identity.user.id,
        user_session_id=identity.session.id,
        settings=settings,
        resume_cursor=resume_cursor,
        disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )

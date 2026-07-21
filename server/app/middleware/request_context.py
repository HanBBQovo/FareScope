from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("farescope.request")
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


class RequestContextMiddleware:
    """Attach a bounded request ID and emit one completion record per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        candidate = headers.get("x-request-id", "")
        request_id = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex
        context_token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request completed request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                request_id,
                scope.get("method"),
                scope.get("path"),
                status_code,
                elapsed_ms,
            )
            request_id_var.reset(context_token)

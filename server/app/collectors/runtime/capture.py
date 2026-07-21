"""Privacy-conscious response capture for page-generated provider requests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.collectors.runtime.models import (
    CaptureDiagnostic,
    CapturedPayload,
    CaptureRule,
    FailureKind,
)


class RequestLike(Protocol):
    method: str


class ResponseLike(Protocol):
    url: str
    status: int
    request: RequestLike

    async def json(self) -> Any: ...


class ResponseCapture:
    """Collect matching JSON payloads in memory without headers or cookies."""

    def __init__(self, *, provider: str, route_key: str, rules: Iterable[CaptureRule]) -> None:
        self.provider = provider
        self.route_key = route_key
        self.rules = tuple(rules)
        self._captures: list[CapturedPayload] = []
        self._diagnostics: list[CaptureDiagnostic] = []
        self._matched_names: set[str] = set()
        self._state_changed = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def captures(self) -> tuple[CapturedPayload, ...]:
        return tuple(self._captures)

    @property
    def diagnostics(self) -> tuple[CaptureDiagnostic, ...]:
        return tuple(self._diagnostics)

    @property
    def matched_names(self) -> frozenset[str]:
        return frozenset(self._matched_names)

    async def handle(self, response: ResponseLike) -> None:
        """Inspect one response; unmatched responses are ignored without reading bodies."""

        method = response.request.method.upper()
        matching_rules = [rule for rule in self.rules if rule.matches(response.url, method)]
        if not matching_rules:
            return

        safe_url = url_without_query(response.url)
        async with self._lock:
            for rule in matching_rules:
                self._matched_names.add(rule.name)

        if response.status == 432:
            diagnostics = [
                CaptureDiagnostic(
                    kind=FailureKind.ANTI_BOT_432,
                    message="Provider returned HTTP 432 for a matched response",
                    provider=self.provider,
                    route_key=self.route_key,
                    capture_name=rule.name,
                    status_code=response.status,
                    url_without_query=safe_url,
                    retryable=True,
                )
                for rule in matching_rules
            ]
            await self._record(diagnostics=diagnostics)
            return

        if not 200 <= response.status < 300:
            diagnostics = [
                CaptureDiagnostic(
                    kind=FailureKind.RESPONSE_STATUS,
                    message="Provider returned a non-success status for a matched response",
                    provider=self.provider,
                    route_key=self.route_key,
                    capture_name=rule.name,
                    status_code=response.status,
                    url_without_query=safe_url,
                    retryable=response.status >= 500 or response.status == 429,
                )
                for rule in matching_rules
            ]
            await self._record(diagnostics=diagnostics)
            return

        try:
            payload = await response.json()
        except Exception as exc:  # noqa: BLE001 - provider/client exceptions vary
            diagnostics = [
                CaptureDiagnostic(
                    kind=FailureKind.RESPONSE_DECODE,
                    message="Matched response body was not readable JSON",
                    provider=self.provider,
                    route_key=self.route_key,
                    capture_name=rule.name,
                    status_code=response.status,
                    url_without_query=safe_url,
                    retryable=True,
                    details={"exception_type": type(exc).__name__},
                )
                for rule in matching_rules
            ]
            await self._record(diagnostics=diagnostics)
            return

        if not isinstance(payload, Mapping):
            await self._record(
                diagnostics=[
                    _schema_diagnostic(
                        provider=self.provider,
                        route_key=self.route_key,
                        rule=rule,
                        status_code=response.status,
                        safe_url=safe_url,
                        message="Matched JSON response was not an object",
                    )
                    for rule in matching_rules
                ]
            )
            return

        captures: list[CapturedPayload] = []
        diagnostics = []
        for rule in matching_rules:
            missing_keys = sorted(rule.required_top_level_keys - payload.keys())
            if missing_keys:
                diagnostics.append(
                    _schema_diagnostic(
                        provider=self.provider,
                        route_key=self.route_key,
                        rule=rule,
                        status_code=response.status,
                        safe_url=safe_url,
                        message="Matched JSON response omitted required envelope keys",
                        details={
                            "missing_keys": ",".join(missing_keys),
                            "top_level_keys": ",".join(
                                sorted(str(key) for key in payload)[:100]
                            ),
                        },
                    )
                )
                continue
            captures.append(
                CapturedPayload(
                    provider=self.provider,
                    route_key=self.route_key,
                    capture_name=rule.name,
                    status_code=response.status,
                    url_without_query=safe_url,
                    received_at=datetime.now(UTC),
                    payload=payload,
                )
            )
        await self._record(captures=captures, diagnostics=diagnostics)

    async def wait_until_terminal(
        self,
        required_names: frozenset[str],
        *,
        timeout_seconds: float,
    ) -> None:
        """Wait for all captures or a definitive response failure."""

        if not required_names:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while not self._is_terminal(required_names):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            self._state_changed.clear()
            await asyncio.wait_for(self._state_changed.wait(), timeout=remaining)

    def has_terminal_failure_for(self, capture_name: str) -> bool:
        return any(
            diagnostic.capture_name == capture_name
            and diagnostic.kind
            in {
                FailureKind.ANTI_BOT_432,
                FailureKind.RESPONSE_STATUS,
                FailureKind.RESPONSE_DECODE,
                FailureKind.SCHEMA_MISSING,
            }
            for diagnostic in self._diagnostics
        )

    def has_anti_bot_failure_for(self, required_names: frozenset[str]) -> bool:
        return any(
            diagnostic.kind == FailureKind.ANTI_BOT_432
            and diagnostic.capture_name in required_names
            for diagnostic in self._diagnostics
        )

    def _is_terminal(self, required_names: frozenset[str]) -> bool:
        captured_names = {capture.capture_name for capture in self._captures}
        missing_names = required_names - captured_names
        return not missing_names or any(
            self.has_terminal_failure_for(name) for name in missing_names
        )

    async def _record(
        self,
        *,
        captures: Iterable[CapturedPayload] = (),
        diagnostics: Iterable[CaptureDiagnostic] = (),
    ) -> None:
        async with self._lock:
            for capture in captures:
                existing_index = next(
                    (
                        index
                        for index, existing in enumerate(self._captures)
                        if existing.capture_name == capture.capture_name
                    ),
                    None,
                )
                if existing_index is None:
                    self._captures.append(capture)
                    continue
                existing = self._captures[existing_index]
                if _payload_richness(capture.payload) >= _payload_richness(existing.payload):
                    self._captures[existing_index] = capture
            self._diagnostics.extend(diagnostics)
            self._state_changed.set()


def ctrip_capture_rules() -> tuple[CaptureRule, ...]:
    """Known page response selectors; these do not construct upstream requests."""

    return (
        CaptureRule(
            name="calendar",
            endpoint_markers=("FlightIntlAndInlandLowestPriceSearch",),
            required_top_level_keys=frozenset({"priceList"}),
        ),
        CaptureRule(
            name="batch_search",
            endpoint_markers=("/batchSearch", "/search/pull/"),
            required_top_level_keys=frozenset({"data"}),
        ),
    )


def url_without_query(url: str) -> str:
    """Remove query, fragment, and any URL user-info before diagnostics are retained."""

    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    safe = SplitResult(parsed.scheme, host, parsed.path, "", "")
    return urlunsplit(safe)


def _schema_diagnostic(
    *,
    provider: str,
    route_key: str,
    rule: CaptureRule,
    status_code: int,
    safe_url: str,
    message: str,
    details: Mapping[str, str | int | bool | None] | None = None,
) -> CaptureDiagnostic:
    return CaptureDiagnostic(
        kind=FailureKind.SCHEMA_MISSING,
        message=message,
        provider=provider,
        route_key=route_key,
        capture_name=rule.name,
        status_code=status_code,
        url_without_query=safe_url,
        retryable=False,
        details=details or {},
    )


def _payload_richness(value: Any, *, depth: int = 0) -> int:
    """Rank envelope responses without inspecting or retaining their scalar values."""

    if depth > 8:
        return 0
    if isinstance(value, Mapping):
        return 1 + sum(_payload_richness(child, depth=depth + 1) for child in value.values())
    if isinstance(value, list):
        return len(value) * 16 + sum(
            _payload_richness(child, depth=depth + 1) for child in value[:64]
        )
    return 0

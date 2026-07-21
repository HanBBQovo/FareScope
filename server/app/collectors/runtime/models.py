"""Value objects shared by browser collection runtime components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class FailureKind(StrEnum):
    """Stable failure labels used by retry and observability layers."""

    ANTI_BOT_432 = "anti_bot_432"
    TIMEOUT = "timeout"
    SCHEMA_MISSING = "schema_missing"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    NAVIGATION_ERROR = "navigation_error"
    RESPONSE_STATUS = "response_status"
    RESPONSE_DECODE = "response_decode"
    SCREENSHOT_FAILED = "screenshot_failed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class CaptureRule:
    """Select one page response without reproducing the provider request."""

    name: str
    endpoint_markers: tuple[str, ...]
    methods: frozenset[str] = frozenset({"POST"})
    required_top_level_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name or not self.endpoint_markers or not self.methods:
            raise ValueError("Capture rules require a name, endpoint marker, and method")
        if any(not marker for marker in self.endpoint_markers):
            raise ValueError("Capture rule endpoint markers must not be empty")
        object.__setattr__(self, "methods", frozenset(method.upper() for method in self.methods))

    def matches(self, url: str, method: str) -> bool:
        normalized_url = url.casefold()
        normalized_method = method.upper()
        return normalized_method in self.methods and any(
            marker.casefold() in normalized_url for marker in self.endpoint_markers
        )


@dataclass(frozen=True, slots=True)
class CapturedPayload:
    """A matched JSON response retained only by the current process."""

    provider: str
    route_key: str
    capture_name: str
    status_code: int
    url_without_query: str
    received_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CaptureDiagnostic:
    kind: FailureKind
    message: str
    provider: str
    route_key: str
    capture_name: str | None = None
    status_code: int | None = None
    url_without_query: str | None = None
    retryable: bool = False
    details: Mapping[str, str | int | bool | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrowserRunResult:
    provider: str
    route_key: str
    started_at: datetime
    finished_at: datetime
    captures: tuple[CapturedPayload, ...]
    diagnostics: tuple[CaptureDiagnostic, ...]
    expected_capture_names: frozenset[str]
    screenshot_path: Path | None = None

    @property
    def captured_names(self) -> frozenset[str]:
        return frozenset(capture.capture_name for capture in self.captures)

    @property
    def missing_capture_names(self) -> frozenset[str]:
        return self.expected_capture_names - self.captured_names

    @property
    def success(self) -> bool:
        fatal_kinds = {
            FailureKind.ANTI_BOT_432,
            FailureKind.BROWSER_UNAVAILABLE,
            FailureKind.NAVIGATION_ERROR,
            FailureKind.TIMEOUT,
            FailureKind.INTERNAL_ERROR,
        }
        return not self.missing_capture_names and not any(
            diagnostic.kind in fatal_kinds
            and (
                diagnostic.capture_name is None
                or diagnostic.capture_name in self.missing_capture_names
            )
            for diagnostic in self.diagnostics
        )

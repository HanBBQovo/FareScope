"""Browser collection runtime primitives.

The runtime intentionally exposes provider-neutral capture, scheduling, and failure
contracts. Provider adapters remain responsible for interpreting captured JSON.
"""

from app.collectors.runtime.browser import BrowserRunConfig, PlaywrightCaptureRunner
from app.collectors.runtime.capture import ResponseCapture, ctrip_capture_rules
from app.collectors.runtime.models import (
    BrowserRunResult,
    CaptureDiagnostic,
    CapturedPayload,
    CaptureRule,
    FailureKind,
)
from app.collectors.runtime.policy import (
    CollectionExecutor,
    ProviderRouteGate,
    RateLimitPolicy,
    RetryPolicy,
)

__all__ = [
    "BrowserRunConfig",
    "BrowserRunResult",
    "CapturedPayload",
    "CaptureDiagnostic",
    "CaptureRule",
    "CollectionExecutor",
    "FailureKind",
    "PlaywrightCaptureRunner",
    "ProviderRouteGate",
    "RateLimitPolicy",
    "ResponseCapture",
    "RetryPolicy",
    "ctrip_capture_rules",
]

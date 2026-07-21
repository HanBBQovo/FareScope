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
    CollectionRateGate,
    ProviderRouteGate,
    RateLimitPolicy,
    RetryPolicy,
)
from app.collectors.runtime.redis_gate import (
    CollectionCoordinationError,
    CollectionCoordinationLeaseLostError,
    CollectionCoordinationTimeoutError,
    CollectionCoordinationUnavailableError,
    RedisAcquireAttempt,
    RedisCoordinationLease,
    RedisLeaseCoordinator,
    RedisProviderRouteGate,
)

__all__ = [
    "BrowserRunConfig",
    "BrowserRunResult",
    "CapturedPayload",
    "CaptureDiagnostic",
    "CaptureRule",
    "CollectionCoordinationError",
    "CollectionCoordinationLeaseLostError",
    "CollectionCoordinationTimeoutError",
    "CollectionCoordinationUnavailableError",
    "CollectionExecutor",
    "CollectionRateGate",
    "FailureKind",
    "PlaywrightCaptureRunner",
    "ProviderRouteGate",
    "RateLimitPolicy",
    "RedisAcquireAttempt",
    "RedisCoordinationLease",
    "RedisLeaseCoordinator",
    "RedisProviderRouteGate",
    "ResponseCapture",
    "RetryPolicy",
    "ctrip_capture_rules",
]

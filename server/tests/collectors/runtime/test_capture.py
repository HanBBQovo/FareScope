from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from app.collectors.runtime.capture import ResponseCapture, ctrip_capture_rules
from app.collectors.runtime.models import BrowserRunResult, FailureKind


@dataclass
class FakeRequest:
    method: str = "POST"


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status: int = 200,
        payload: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self.request = FakeRequest()
        self.payload = payload
        self.error = error

    async def json(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.mark.asyncio
async def test_captures_matching_calendar_payload_without_query_or_headers() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())
    payload = {
        "priceList": [],
        "responseStatus": {"ack": "Success"},
    }

    await capture.handle(
        FakeResponse(
            url=(
                "https://flights.ctrip.com/api/FlightIntlAndInlandLowestPriceSearch"
                "?token=must-not-remain#fragment"
            ),
            payload=payload,
        )
    )

    assert len(capture.captures) == 1
    item = capture.captures[0]
    assert item.capture_name == "calendar"
    assert item.payload is payload
    assert item.url_without_query.endswith("/FlightIntlAndInlandLowestPriceSearch")
    assert "token" not in item.url_without_query
    assert capture.diagnostics == ()


@pytest.mark.asyncio
async def test_unmatched_response_body_is_not_read() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())
    response = FakeResponse(
        url="https://flights.ctrip.com/unrelated",
        error=AssertionError("body must not be read"),
    )

    await capture.handle(response)

    assert capture.captures == ()
    assert capture.diagnostics == ()


@pytest.mark.asyncio
async def test_http_432_has_stable_retryable_failure_kind() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())

    await capture.handle(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch?secret=x",
            status=432,
        )
    )
    await capture.wait_until_terminal(
        frozenset({"batch_search"}),
        timeout_seconds=0.01,
    )

    diagnostic = capture.diagnostics[0]
    assert diagnostic.kind == FailureKind.ANTI_BOT_432
    assert diagnostic.retryable is True
    assert diagnostic.status_code == 432
    assert "secret" not in (diagnostic.url_without_query or "")


@pytest.mark.asyncio
async def test_later_success_recovers_a_capture_from_an_earlier_432() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())
    url = "https://flights.ctrip.com/api/batchSearch"
    await capture.handle(FakeResponse(url=url, status=432))
    await capture.handle(
        FakeResponse(
            url=url,
            status=200,
            payload={"status": 0, "data": {"flightItineraryList": []}},
        )
    )
    now = datetime.now(UTC)
    result = BrowserRunResult(
        provider="ctrip",
        route_key="SHA-TYO",
        started_at=now,
        finished_at=now,
        captures=capture.captures,
        diagnostics=capture.diagnostics,
        expected_capture_names=frozenset({"batch_search"}),
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_missing_envelope_is_schema_missing_not_a_crash() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())

    await capture.handle(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch",
            payload={"status": 0},
        )
    )

    assert capture.captures == ()
    assert capture.matched_names == frozenset({"batch_search"})
    assert capture.diagnostics[0].kind == FailureKind.SCHEMA_MISSING
    assert capture.diagnostics[0].details == {
        "missing_keys": "data",
        "top_level_keys": "status",
    }

    await capture.wait_until_terminal(
        frozenset({"batch_search"}),
        timeout_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_richer_pull_response_replaces_an_empty_batch_envelope() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())
    empty_envelope = {
        "status": 0,
        "data": {"context": {"finished": False}},
    }
    detailed_envelope = {
        "status": 0,
        "data": {
            "context": {"finished": False},
            "flightItineraryList": [{"itineraryId": "safe-test-id"}],
        },
    }

    await capture.handle(
        FakeResponse(
            url="https://flights.ctrip.com/international/search/api/search/batchSearch",
            payload=empty_envelope,
        )
    )
    await capture.handle(
        FakeResponse(
            url=(
                "https://flights.ctrip.com/international/search/api/search/pull/"
                "safe-token"
            ),
            payload=detailed_envelope,
        )
    )

    assert len(capture.captures) == 1
    assert capture.captures[0].payload is detailed_envelope
    assert "/search/pull/" in capture.captures[0].url_without_query


@pytest.mark.asyncio
async def test_upstream_error_is_terminal_without_waiting_for_deadline() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())
    await capture.handle(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch",
            status=503,
        )
    )

    await capture.wait_until_terminal(
        frozenset({"batch_search"}),
        timeout_seconds=0.01,
    )

    assert capture.diagnostics[0].kind == FailureKind.RESPONSE_STATUS
    assert capture.diagnostics[0].retryable is True


@pytest.mark.asyncio
async def test_json_decode_failure_is_diagnostic() -> None:
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=ctrip_capture_rules())

    await capture.handle(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch",
            error=ValueError("redacted"),
        )
    )

    assert capture.diagnostics[0].kind == FailureKind.RESPONSE_DECODE
    assert capture.diagnostics[0].details == {"exception_type": "ValueError"}

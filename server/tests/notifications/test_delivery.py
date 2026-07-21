from __future__ import annotations

import httpx
import pytest

from app.services.notification_delivery import DeliveryWork, deliver_work


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    response: _FakeResponse = _FakeResponse(204)
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, object]) -> _FakeResponse:
        self.calls.append((url, json))
        return self.response


def _work(channel_type: str, destination: str) -> DeliveryWork:
    from uuid import uuid4

    return DeliveryWork(
        delivery_id=uuid4(),
        channel_id=uuid4(),
        channel_type=channel_type,
        destination=destination,
        title="FareScope test",
        body="price changed",
        payload={"priceMinor": 12300},
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_webhook_adapter_sends_only_safe_event_payload(monkeypatch) -> None:
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await deliver_work(_work("webhook", "https://hooks.example.test/fare"))

    assert result.success is True
    assert result.response_metadata == {"httpStatus": 204}
    assert _FakeClient.calls == [
        (
            "https://hooks.example.test/fare",
            {"title": "FareScope test", "body": "price changed", "event": {"priceMinor": 12300}},
        )
    ]


@pytest.mark.asyncio
async def test_provider_server_error_is_retryable(monkeypatch) -> None:
    _FakeClient.response = _FakeResponse(503)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await deliver_work(_work("webhook", "https://hooks.example.test/fare"))

    assert result.success is False
    assert result.retryable is True
    assert result.error_code == "http_503"
    _FakeClient.response = _FakeResponse(204)


@pytest.mark.asyncio
async def test_invalid_webhook_destination_is_terminal(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await deliver_work(_work("webhook", "http://127.0.0.1:8080/internal"))

    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "invalid_destination_response"


@pytest.mark.asyncio
async def test_email_is_explicitly_not_claimed_as_delivered() -> None:
    result = await deliver_work(_work("email", "alerts@example.com"))

    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "email_delivery_not_configured"


@pytest.mark.asyncio
async def test_telegram_token_with_colon_is_kept_intact(monkeypatch) -> None:
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse(200, {"ok": True, "result": {"message_id": 42}})
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await deliver_work(_work("telegram", "123456:ABCDEF|-100123"))

    assert result.success is True
    assert result.provider_message_id == "42"
    assert _FakeClient.calls[0][0] == "https://api.telegram.org/bot123456:ABCDEF/sendMessage"
    assert _FakeClient.calls[0][1]["chat_id"] == "-100123"

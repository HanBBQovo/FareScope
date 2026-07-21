"""Durable notification delivery workers and provider adapters.

The database owns delivery state. Network calls happen after rows are claimed and are
never made while a transaction is open. Secrets are decrypted only in the worker's
memory and are excluded from logs and response metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertEvent, NotificationChannel, NotificationDelivery
from app.models.enums import DeliveryStatus
from app.security import SecretBox, SecretDecryptionError


@dataclass(frozen=True, slots=True)
class DeliveryWork:
    delivery_id: UUID
    channel_id: UUID
    channel_type: str
    destination: str
    title: str
    body: str
    payload: dict[str, object]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    success: bool
    retryable: bool
    provider_message_id: str | None = None
    response_metadata: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None


async def claim_pending_deliveries(
    session: AsyncSession,
    *,
    secret_box: SecretBox,
    now: datetime | None = None,
    limit: int = 50,
    stale_after_seconds: int = 900,
) -> list[DeliveryWork]:
    now = now or datetime.now(UTC)
    stale_before = now - timedelta(seconds=stale_after_seconds)
    statement = (
        select(NotificationDelivery, NotificationChannel, AlertEvent)
        .join(
            NotificationChannel,
            NotificationChannel.id == NotificationDelivery.notification_channel_id,
        )
        .join(AlertEvent, AlertEvent.id == NotificationDelivery.alert_event_id)
        .where(
            NotificationChannel.enabled.is_(True),
            or_(
                (
                    NotificationDelivery.status.in_(
                        [DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value]
                    )
                    & (
                        NotificationDelivery.next_attempt_at.is_(None)
                        | (NotificationDelivery.next_attempt_at <= now)
                    )
                ),
                (
                    (NotificationDelivery.status == DeliveryStatus.SENDING.value)
                    & (NotificationDelivery.updated_at <= stale_before)
                ),
            ),
        )
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
        .limit(min(limit, 200))
        .with_for_update(skip_locked=True)
    )
    rows = (await session.execute(statement)).all()
    works: list[DeliveryWork] = []
    for delivery, channel, event in rows:
        try:
            if channel.secret_ciphertext is None:
                raise SecretDecryptionError("notification channel has no encrypted destination")
            secret = secret_box.decrypt_mapping(channel.secret_ciphertext)
            destination = secret.get("destination")
            if not isinstance(destination, str) or not destination:
                raise SecretDecryptionError("notification destination is invalid")
        except SecretDecryptionError as error:
            delivery.status = DeliveryStatus.FAILED.value
            delivery.error_code = "secret_unavailable"
            delivery.error_message = str(error)
            delivery.next_attempt_at = None
            continue
        delivery.status = DeliveryStatus.SENDING.value
        delivery.attempt_count += 1
        delivery.error_code = None
        delivery.error_message = None
        delivery.next_attempt_at = None
        works.append(
            DeliveryWork(
                delivery_id=delivery.id,
                channel_id=channel.id,
                channel_type=channel.channel_type,
                destination=destination,
                title=event.title,
                body=event.body,
                payload=dict(event.event_payload or {}),
                attempt_count=delivery.attempt_count,
            )
        )
    await session.flush()
    return works


async def deliver_work(
    work: DeliveryWork,
    *,
    timeout_seconds: float = 10.0,
) -> DeliveryResult:
    try:
        if work.channel_type == "webhook":
            return await _send_webhook(work, timeout_seconds=timeout_seconds)
        if work.channel_type == "pushplus":
            return await _send_pushplus(work, timeout_seconds=timeout_seconds)
        if work.channel_type == "bark":
            return await _send_bark(work, timeout_seconds=timeout_seconds)
        if work.channel_type == "telegram":
            return await _send_telegram(work, timeout_seconds=timeout_seconds)
        if work.channel_type == "email":
            return DeliveryResult(
                success=False,
                retryable=False,
                error_code="email_delivery_not_configured",
                error_message="SMTP delivery is not configured on this installation",
            )
        return DeliveryResult(
            success=False,
            retryable=False,
            error_code="unsupported_channel",
            error_message="notification channel is not supported",
        )
    except (httpx.TimeoutException, httpx.NetworkError) as error:
        return DeliveryResult(
            success=False,
            retryable=True,
            error_code="network_error",
            error_message=type(error).__name__,
        )
    except (ValueError, json.JSONDecodeError) as error:
        return DeliveryResult(
            success=False,
            retryable=False,
            error_code="invalid_destination_response",
            error_message=str(error)[:240],
        )


async def finish_delivery(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    result: DeliveryResult,
    now: datetime | None = None,
    max_attempts: int = 5,
    retry_base_seconds: int = 60,
) -> None:
    now = now or datetime.now(UTC)
    delivery = await session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None:
        return
    if result.success:
        delivery.status = DeliveryStatus.SUCCEEDED.value
        delivery.sent_at = now
        delivery.provider_message_id = result.provider_message_id
        delivery.response_metadata = result.response_metadata or {}
        delivery.next_attempt_at = None
        delivery.error_code = None
        delivery.error_message = None
    else:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.error_code = result.error_code or "delivery_failed"
        delivery.error_message = (result.error_message or "delivery failed")[:1000]
        delivery.response_metadata = result.response_metadata or {}
        if result.retryable and delivery.attempt_count < max_attempts:
            delay = min(retry_base_seconds * (2 ** max(delivery.attempt_count - 1, 0)), 86_400)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
        else:
            delivery.next_attempt_at = None
    await session.flush()


async def _send_webhook(work: DeliveryWork, *, timeout_seconds: float) -> DeliveryResult:
    _require_https_url(work.destination)
    payload = {
        "title": work.title,
        "body": work.body,
        "event": work.payload,
    }
    async with httpx.AsyncClient(
        timeout=timeout_seconds, follow_redirects=False, headers={"User-Agent": "FareScope/0.1"}
    ) as client:
        response = await client.post(work.destination, json=payload)
    return _http_result(response)


async def _send_pushplus(work: DeliveryWork, *, timeout_seconds: float) -> DeliveryResult:
    if not work.destination.strip():
        raise ValueError("PushPlus token is empty")
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        response = await client.post(
            "https://www.pushplus.plus/send",
            json={"token": work.destination, "title": work.title, "content": work.body},
        )
    result = _http_result(response)
    if result.success:
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("code") not in (None, 200):
                return DeliveryResult(
                    success=False,
                    retryable=False,
                    response_metadata={"httpStatus": response.status_code},
                    error_code="provider_rejected",
                    error_message="PushPlus rejected the message",
                )
        except ValueError:
            pass
    return result


async def _send_bark(work: DeliveryWork, *, timeout_seconds: float) -> DeliveryResult:
    destination = work.destination.strip()
    if not destination.startswith("https://"):
        destination = f"https://api.day.app/{destination.strip('/') }"
    _require_https_url(destination)
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        response = await client.post(
            destination,
            json={"title": work.title, "body": work.body, "group": "farescope"},
        )
    return _http_result(response)


async def _send_telegram(work: DeliveryWork, *, timeout_seconds: float) -> DeliveryResult:
    token, separator, chat_id = work.destination.partition("|")
    if not separator:
        token, separator, chat_id = work.destination.rpartition(":")
    if not separator or not token or not chat_id:
        raise ValueError("Telegram destination must be BOT_TOKEN|CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        response = await client.post(
            url,
            json={"chat_id": chat_id, "text": f"{work.title}\n{work.body}"},
        )
    result = _http_result(response)
    if result.success:
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("ok") is False:
                return DeliveryResult(
                    success=False,
                    retryable=False,
                    response_metadata={"httpStatus": response.status_code},
                    error_code="provider_rejected",
                    error_message="Telegram rejected the message",
                )
            message_id = (
                body.get("result", {}).get("message_id") if isinstance(body, dict) else None
            )
            return DeliveryResult(
                success=True,
                retryable=False,
                provider_message_id=str(message_id) if message_id is not None else None,
                response_metadata={"httpStatus": response.status_code},
            )
        except ValueError:
            pass
    return result


def _require_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("notification URL must be an HTTPS URL without credentials")


def _http_result(response: httpx.Response) -> DeliveryResult:
    status = response.status_code
    metadata = {"httpStatus": status}
    if 200 <= status < 300:
        return DeliveryResult(success=True, retryable=False, response_metadata=metadata)
    retryable = status == 429 or status >= 500
    return DeliveryResult(
        success=False,
        retryable=retryable,
        response_metadata=metadata,
        error_code=f"http_{status}",
        error_message="notification provider returned an error",
    )

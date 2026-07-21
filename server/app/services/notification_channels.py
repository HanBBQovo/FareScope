from __future__ import annotations

import ipaddress
from datetime import time
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification_schedule import (
    NotificationScheduleError,
    validate_notification_schedule,
)
from app.models import AuditEvent, NotificationChannel, User
from app.security import SecretBox


class NotificationChannelError(Exception):
    pass


class NotificationChannelNotFoundError(NotificationChannelError):
    pass


class NotificationChannelConflictError(NotificationChannelError):
    pass


async def list_notification_channels(
    session: AsyncSession, *, user_id: UUID
) -> list[NotificationChannel]:
    return list(
        (
            await session.scalars(
                select(NotificationChannel)
                .where(NotificationChannel.user_id == user_id)
                .order_by(NotificationChannel.created_at, NotificationChannel.id)
                .limit(100)
            )
        ).all()
    )


async def create_notification_channel(
    session: AsyncSession,
    *,
    user: User,
    channel_type: str,
    label: str,
    destination: str,
    secret_box: SecretBox,
    timezone: str | None = None,
    quiet_hours_start: time | None = None,
    quiet_hours_end: time | None = None,
    allowed_weekdays: list[int] | None = None,
) -> NotificationChannel:
    try:
        normalized_timezone, normalized_weekdays = validate_notification_schedule(
            timezone_name=timezone,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            allowed_weekdays=allowed_weekdays,
        )
    except NotificationScheduleError as error:
        raise NotificationChannelError(str(error)) from error
    normalized_label = label.strip()
    existing = await session.scalar(
        select(NotificationChannel.id).where(
            NotificationChannel.user_id == user.id,
            NotificationChannel.name == normalized_label,
        )
    )
    if existing is not None:
        raise NotificationChannelConflictError("a channel with this name already exists")

    normalized_destination = _normalize_destination(channel_type, destination)
    masked_destination = mask_destination(channel_type, normalized_destination)
    channel = NotificationChannel(
        user_id=user.id,
        name=normalized_label,
        channel_type=channel_type,
        enabled=True,
        secret_ciphertext=secret_box.encrypt_mapping(
            {"destination": normalized_destination}
        ),
        config_redacted={"destination_masked": masked_destination},
        timezone=normalized_timezone,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        allowed_weekdays=(
            list(normalized_weekdays) if normalized_weekdays is not None else None
        ),
    )
    session.add(channel)
    await session.flush()
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="notification_channel.created",
            target_type="notification_channel",
            target_id=str(channel.id),
            metadata_json={"channel_type": channel_type},
            summary=f"Notification channel created: {channel.name}",
        )
    )
    return channel


async def update_notification_channel(
    session: AsyncSession,
    *,
    user: User,
    channel_id: UUID,
    updates: dict[str, object],
) -> NotificationChannel:
    channel = await session.scalar(
        select(NotificationChannel)
        .where(NotificationChannel.id == channel_id, NotificationChannel.user_id == user.id)
        .with_for_update()
    )
    if channel is None:
        raise NotificationChannelNotFoundError
    if not updates:
        raise NotificationChannelError("at least one notification channel field is required")

    timezone = updates.get("timezone", channel.timezone)
    quiet_hours_start = updates.get("quiet_hours_start", channel.quiet_hours_start)
    quiet_hours_end = updates.get("quiet_hours_end", channel.quiet_hours_end)
    allowed_weekdays = updates.get("allowed_weekdays", channel.allowed_weekdays)
    if timezone is not None and not isinstance(timezone, str):
        raise NotificationChannelError("timezone is invalid")
    if quiet_hours_start is not None and not isinstance(quiet_hours_start, time):
        raise NotificationChannelError("quiet hours start is invalid")
    if quiet_hours_end is not None and not isinstance(quiet_hours_end, time):
        raise NotificationChannelError("quiet hours end is invalid")
    if allowed_weekdays is not None and not isinstance(allowed_weekdays, (list, tuple)):
        raise NotificationChannelError("allowed weekdays are invalid")
    try:
        normalized_timezone, normalized_weekdays = validate_notification_schedule(
            timezone_name=timezone,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            allowed_weekdays=allowed_weekdays,
        )
    except (NotificationScheduleError, TypeError) as error:
        raise NotificationChannelError(str(error)) from error

    if "enabled" in updates:
        channel.enabled = bool(updates["enabled"])
    channel.timezone = normalized_timezone
    channel.quiet_hours_start = quiet_hours_start
    channel.quiet_hours_end = quiet_hours_end
    channel.allowed_weekdays = (
        list(normalized_weekdays) if normalized_weekdays is not None else None
    )
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="notification_channel.updated",
            target_type="notification_channel",
            target_id=str(channel.id),
            metadata_json={"fields": sorted(updates)},
            summary="Notification channel state changed",
        )
    )
    await session.flush()
    return channel


async def set_notification_channel_enabled(
    session: AsyncSession,
    *,
    user: User,
    channel_id: UUID,
    enabled: bool,
) -> NotificationChannel:
    return await update_notification_channel(
        session,
        user=user,
        channel_id=channel_id,
        updates={"enabled": enabled},
    )


def mask_destination(channel_type: str, destination: str) -> str:
    if channel_type == "email":
        local, domain = destination.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if channel_type == "webhook":
        parsed = urlsplit(destination)
        return f"{parsed.scheme}://{parsed.netloc}/***"
    visible = destination[-4:] if len(destination) > 4 else destination[-1:]
    return f"***{visible}"


def _normalize_destination(channel_type: str, destination: str) -> str:
    value = destination.strip()
    if channel_type == "email":
        raise NotificationChannelError("email delivery is not configured")
    if channel_type == "webhook":
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise NotificationChannelError("webhook destination must be an HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise NotificationChannelError("webhook URL must not contain credentials")
        _reject_private_host(parsed.hostname)
    elif channel_type == "telegram":
        token, separator, chat_id = value.partition("|")
        if not separator:
            token, separator, chat_id = value.rpartition(":")
        if not separator or not token.strip() or not chat_id.strip():
            raise NotificationChannelError("Telegram destination must be BOT_TOKEN|CHAT_ID")
    elif channel_type == "bark" and value.startswith("https://"):
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise NotificationChannelError("Bark URL must not contain credentials")
        _reject_private_host(parsed.hostname or "")
    elif channel_type == "pushplus" and not value:
        raise NotificationChannelError("PushPlus token is empty")
    return value


def _reject_private_host(hostname: str) -> None:
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized or normalized in {"localhost", "localhost.localdomain"}:
        raise NotificationChannelError("notification host is not allowed")
    if normalized.endswith((".local", ".internal", ".lan")):
        raise NotificationChannelError("notification host is not allowed")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise NotificationChannelError("notification host is not allowed")

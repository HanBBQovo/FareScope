from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    DatabaseSession,
    IdentityDependency,
    SettingsDependency,
    require_csrf,
)
from app.api.schemas.fares import ResponseMeta
from app.api.schemas.notifications import (
    NotificationChannelCreateRequest,
    NotificationChannelListResponse,
    NotificationChannelPublic,
    NotificationChannelUpdateRequest,
)
from app.models import NotificationChannel
from app.security import InvalidEncryptionKeyError, SecretBox
from app.services.notification_channels import (
    NotificationChannelConflictError,
    NotificationChannelError,
    NotificationChannelNotFoundError,
    create_notification_channel,
    list_notification_channels,
    set_notification_channel_enabled,
)

router = APIRouter()


def _serialize(channel: NotificationChannel) -> NotificationChannelPublic:
    return NotificationChannelPublic(
        id=channel.id,
        type=channel.channel_type,
        label=channel.name,
        destinationMasked=str(channel.config_redacted.get("destination_masked", "***")),
        enabled=channel.enabled,
        verifiedAt=channel.verified_at,
    )


def _secret_box(settings: SettingsDependency) -> SecretBox:
    key = settings.secret_encryption_key
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="notification encryption key is not configured",
        )
    try:
        return SecretBox(key.get_secret_value())
    except InvalidEncryptionKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="notification encryption key is invalid",
        ) from error


@router.get("/channels", response_model=NotificationChannelListResponse)
async def get_channels(
    identity: IdentityDependency,
    database: DatabaseSession,
) -> NotificationChannelListResponse:
    channels = await list_notification_channels(database, user_id=identity.user.id)
    return NotificationChannelListResponse(
        meta=ResponseMeta(generatedAt=datetime.now(UTC)),
        items=[_serialize(channel) for channel in channels],
    )


@router.post(
    "/channels",
    response_model=NotificationChannelPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def add_channel(
    payload: NotificationChannelCreateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> NotificationChannelPublic:
    secret_box = _secret_box(settings)
    try:
        async with database.begin():
            channel = await create_notification_channel(
                database,
                user=identity.user,
                channel_type=payload.type,
                label=payload.label,
                destination=payload.destination,
                secret_box=secret_box,
            )
    except NotificationChannelConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except NotificationChannelError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return _serialize(channel)


@router.patch(
    "/channels/{channel_id}",
    response_model=NotificationChannelPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_channel(
    channel_id: UUID,
    payload: NotificationChannelUpdateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> NotificationChannelPublic:
    try:
        async with database.begin():
            channel = await set_notification_channel_enabled(
                database,
                user=identity.user,
                channel_id=channel_id,
                enabled=payload.enabled,
            )
    except NotificationChannelNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return _serialize(channel)

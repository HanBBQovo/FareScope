from __future__ import annotations

import os
from datetime import time
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import User
from app.security import SecretBox
from app.services.notification_channels import (
    NotificationChannelError,
    create_notification_channel,
    update_notification_channel,
)

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_channel_schedule_is_persisted_updated_and_clearable() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            suffix = uuid4().hex
            user = User(
                username=f"channel-{suffix}",
                normalized_username=f"channel-{suffix}",
                display_name="Channel schedule test",
                role="member",
                status="active",
            )
            session.add(user)
            await session.flush()
            secret_box = SecretBox(Fernet.generate_key().decode())

            channel = await create_notification_channel(
                session,
                user=user,
                channel_type="webhook",
                label="Quiet webhook",
                destination="https://hooks.example.test/fare",
                secret_box=secret_box,
                timezone="Asia/Shanghai",
                quiet_hours_start=time(22),
                quiet_hours_end=time(8),
                allowed_weekdays=[0, 1, 2, 3, 4],
            )

            assert channel.timezone == "Asia/Shanghai"
            assert channel.quiet_hours_start == time(22)
            assert channel.allowed_weekdays == [0, 1, 2, 3, 4]
            assert b"hooks.example.test" not in (channel.secret_ciphertext or b"")
            assert "destination" not in channel.config_redacted

            cleared = await update_notification_channel(
                session,
                user=user,
                channel_id=channel.id,
                updates={
                    "timezone": None,
                    "quiet_hours_start": None,
                    "quiet_hours_end": None,
                    "allowed_weekdays": None,
                },
            )
            assert cleared.timezone is None
            assert cleared.quiet_hours_start is None
            assert cleared.allowed_weekdays is None

            with pytest.raises(NotificationChannelError, match="timezone is required"):
                await update_notification_channel(
                    session,
                    user=user,
                    channel_id=channel.id,
                    updates={"allowed_weekdays": [0]},
                )
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()

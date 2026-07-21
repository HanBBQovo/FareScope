from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, User, UserSession
from app.models.enums import UserRole, UserStatus
from app.security import hash_password, issue_secret_token, verify_password

BOOTSTRAP_LOCK_ID = 1_179_668_294
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")


class IdentityError(Exception):
    pass


class IdentityConflictError(IdentityError):
    pass


class InvalidCredentialsError(IdentityError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    session: UserSession
    raw_token: str


def normalize_username(username: str) -> str:
    """Return the canonical login name or reject an unsafe/ambiguous value."""

    if not isinstance(username, str):
        raise ValueError("username must be a string")
    normalized = username.strip().casefold()
    if USERNAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "username must be 3-64 characters and contain only letters, numbers, _, ., or -"
        )
    return normalized


async def create_initial_admin(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    session_ttl_seconds: int,
    user_agent: str | None,
) -> IssuedSession:
    await session.execute(text(f"SELECT pg_advisory_xact_lock({BOOTSTRAP_LOCK_ID})"))
    existing_count = await session.scalar(select(func.count()).select_from(User))
    if existing_count:
        raise IdentityConflictError("initial administrator already exists")

    normalized_username = normalize_username(username)
    user = User(
        username=normalized_username,
        normalized_username=normalized_username,
        password_hash=hash_password(password),
        display_name=normalized_username,
        role=UserRole.ADMIN.value,
        status=UserStatus.ACTIVE.value,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="identity.bootstrap_admin",
            target_type="user",
            target_id=str(user.id),
            summary="Initial administrator created",
        )
    )
    return await issue_user_session(
        session,
        user=user,
        ttl_seconds=session_ttl_seconds,
        user_agent=user_agent,
    )


async def authenticate(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    session_ttl_seconds: int,
    user_agent: str | None,
) -> IssuedSession:
    normalized_username = normalize_username(username)
    user = await session.scalar(
        select(User).where(User.normalized_username == normalized_username).with_for_update()
    )
    if (
        user is None
        or user.status != UserStatus.ACTIVE.value
        or user.password_hash is None
        or not verify_password(password, user.password_hash)
    ):
        raise InvalidCredentialsError("invalid username or password")

    now = datetime.now(UTC)
    user.last_login_at = now
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="identity.login",
            target_type="session",
            summary="User signed in",
        )
    )
    return await issue_user_session(
        session,
        user=user,
        ttl_seconds=session_ttl_seconds,
        user_agent=user_agent,
    )


async def register_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    session_ttl_seconds: int,
    user_agent: str | None,
) -> IssuedSession:
    """Create a normal member account without introducing team/invite workflow."""

    normalized_username = normalize_username(username)
    existing_user = await session.scalar(
        select(User.id).where(User.normalized_username == normalized_username)
    )
    if existing_user is not None:
        raise IdentityConflictError("a user with this username already exists")

    now = datetime.now(UTC)
    user = User(
        username=normalized_username,
        normalized_username=normalized_username,
        password_hash=hash_password(password),
        display_name=normalized_username,
        role=UserRole.MEMBER.value,
        status=UserStatus.ACTIVE.value,
        last_login_at=now,
    )
    session.add(user)
    await session.flush()
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="identity.register",
            target_type="user",
            target_id=str(user.id),
            summary="Member account registered",
        )
    )
    return await issue_user_session(
        session,
        user=user,
        ttl_seconds=session_ttl_seconds,
        user_agent=user_agent,
    )


async def issue_user_session(
    session: AsyncSession,
    *,
    user: User,
    ttl_seconds: int,
    user_agent: str | None,
) -> IssuedSession:
    token = issue_secret_token("fs_session")
    user_session = UserSession(
        user_id=user.id,
        token_hash=token.digest,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        user_agent=(user_agent or "")[:512] or None,
    )
    session.add(user_session)
    await session.flush()
    return IssuedSession(user=user, session=user_session, raw_token=token.value)


async def revoke_session(
    session: AsyncSession, *, user: User, user_session: UserSession
) -> None:
    if user_session.revoked_at is None:
        user_session.revoked_at = datetime.now(UTC)
        session.add(user_session)
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                action="identity.logout",
                target_type="session",
                target_id=str(user_session.id),
                summary="User signed out",
            )
        )

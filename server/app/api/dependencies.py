from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User, UserSession
from app.models.enums import UserRole, UserStatus
from app.security import token_digest
from app.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class CurrentIdentity:
    user: User
    session: UserSession


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
FunctionDatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session, scope="function"),
]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def get_current_identity(
    request: Request,
    settings: SettingsDependency,
) -> CurrentIdentity:
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as database:
        row = (
            await database.execute(
                select(User, UserSession)
                .join(UserSession, UserSession.user_id == User.id)
                .where(
                    UserSession.token_hash == token_digest(session_token),
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > datetime.now(UTC),
                    User.status == UserStatus.ACTIVE.value,
                )
            )
        ).one_or_none()
        if row is not None:
            database.expunge(row[0])
            database.expunge(row[1])
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user, user_session = row
    return CurrentIdentity(user=user, session=user_session)


IdentityDependency = Annotated[CurrentIdentity, Depends(get_current_identity)]


async def require_admin(
    identity: IdentityDependency,
) -> CurrentIdentity:
    if identity.user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return identity


AdminIdentityDependency = Annotated[CurrentIdentity, Depends(require_admin)]


async def require_csrf(
    request: Request,
    settings: SettingsDependency,
) -> None:
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    csrf_header = request.headers.get("X-CSRF-Token")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

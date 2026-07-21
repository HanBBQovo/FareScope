from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import (
    DatabaseSession,
    IdentityDependency,
    SettingsDependency,
    require_csrf,
)
from app.api.schemas.identity import (
    AuthenticatedUser,
    BootstrapAdminRequest,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
)
from app.security import issue_secret_token
from app.services.identity import (
    IdentityConflictError,
    InvalidCredentialsError,
    authenticate,
    create_initial_admin,
    register_user,
    revoke_session,
)
from app.settings import Settings

router = APIRouter()


def _set_auth_cookies(
    response: Response, *, session_token: str, settings: Settings
) -> None:
    csrf_token = issue_secret_token("fs_csrf", entropy_bytes=24).value
    cookie_options = {
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_ttl_seconds,
    }
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        httponly=True,
        **cookie_options,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        **cookie_options,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post(
    "/bootstrap",
    response_model=AuthenticatedUser,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
    bootstrap_token: Annotated[str | None, Header(alias="X-Bootstrap-Token")] = None,
) -> AuthenticatedUser:
    expected = settings.bootstrap_admin_token
    if expected is None or bootstrap_token is None or not hmac.compare_digest(
        bootstrap_token, expected.get_secret_value()
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bootstrap disabled")
    try:
        async with database.begin():
            issued = await create_initial_admin(
                database,
                username=payload.username,
                password=payload.password,
                session_ttl_seconds=settings.session_ttl_seconds,
                user_agent=_user_agent(request),
            )
    except IdentityConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="administrator could not be created",
        ) from error

    _set_auth_cookies(response, session_token=issued.raw_token, settings=settings)
    return AuthenticatedUser(user=issued.user)


@router.post("/login", response_model=AuthenticatedUser)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AuthenticatedUser:
    try:
        async with database.begin():
            issued = await authenticate(
                database,
                username=payload.username,
                password=payload.password,
                session_ttl_seconds=settings.session_ttl_seconds,
                user_agent=_user_agent(request),
            )
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        ) from error

    _set_auth_cookies(response, session_token=issued.raw_token, settings=settings)
    return AuthenticatedUser(user=issued.user)


@router.post(
    "/register",
    response_model=AuthenticatedUser,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AuthenticatedUser:
    if not settings.public_registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="public registration is disabled",
        )
    try:
        async with database.begin():
            issued = await register_user(
                database,
                username=payload.username,
                password=payload.password,
                session_ttl_seconds=settings.session_ttl_seconds,
                user_agent=_user_agent(request),
            )
    except IdentityConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="account could not be created",
        ) from error

    _set_auth_cookies(response, session_token=issued.raw_token, settings=settings)
    return AuthenticatedUser(user=issued.user)


@router.get("/me", response_model=AuthenticatedUser)
async def current_user(
    identity: IdentityDependency,
) -> AuthenticatedUser:
    return AuthenticatedUser(user=identity.user)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    dependencies=[Depends(require_csrf)],
)
async def logout(
    response: Response,
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> LogoutResponse:
    async with database.begin():
        await revoke_session(database, user=identity.user, user_session=identity.session)
    _clear_auth_cookies(response, settings)
    return LogoutResponse()

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.security.passwords import MIN_PASSWORD_LENGTH

USERNAME_PATTERN = r"^[a-z0-9][a-z0-9_.-]{2,63}$"


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: EmailStr | None = None
    display_name: str
    role: Literal["admin", "member"]
    status: Literal["pending", "active", "disabled"]
    last_login_at: datetime | None
    created_at: datetime


class _UsernameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[
        str,
        Field(
            min_length=3,
            max_length=64,
            pattern=USERNAME_PATTERN,
        ),
    ]

    @field_validator("username", mode="before")
    @classmethod
    def canonicalize_username(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip().casefold()
        return value


class BootstrapAdminRequest(_UsernameRequest):
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)]


class LoginRequest(_UsernameRequest):
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class RegisterRequest(_UsernameRequest):
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)]


class AuthenticatedUser(BaseModel):
    user: UserPublic


class LogoutResponse(BaseModel):
    status: Literal["ok"] = "ok"

from datetime import datetime, time
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas.fares import ResponseMeta
from app.domain.notification_schedule import (
    normalize_allowed_weekdays,
    validate_notification_schedule,
    validate_timezone_name,
)

ChannelType = Literal["email", "telegram", "bark", "pushplus", "webhook"]


class NotificationChannelCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: ChannelType
    label: Annotated[str, Field(min_length=1, max_length=120)]
    destination: Annotated[str, Field(min_length=1, max_length=2048)]
    timezone: Annotated[str | None, Field(max_length=64)] = None
    quiet_hours_start: time | None = Field(default=None, alias="quietHoursStart")
    quiet_hours_end: time | None = Field(default=None, alias="quietHoursEnd")
    allowed_weekdays: list[int] | None = Field(default=None, alias="allowedWeekdays")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        normalized = normalize_allowed_weekdays(value)
        return list(normalized) if normalized is not None else None

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        validate_notification_schedule(
            timezone_name=self.timezone,
            quiet_hours_start=self.quiet_hours_start,
            quiet_hours_end=self.quiet_hours_end,
            allowed_weekdays=self.allowed_weekdays,
        )
        return self


class NotificationChannelUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool | None = None
    timezone: Annotated[str | None, Field(max_length=64)] = None
    quiet_hours_start: time | None = Field(default=None, alias="quietHoursStart")
    quiet_hours_end: time | None = Field(default=None, alias="quietHoursEnd")
    allowed_weekdays: list[int] | None = Field(default=None, alias="allowedWeekdays")

    @model_validator(mode="after")
    def validate_explicit_enabled(self) -> Self:
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("enabled cannot be null")
        return self

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        normalized = normalize_allowed_weekdays(value)
        return list(normalized) if normalized is not None else None


class NotificationChannelPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    type: ChannelType
    label: str
    destination_masked: str = Field(alias="destinationMasked")
    enabled: bool
    timezone: str | None
    quiet_hours_start: time | None = Field(alias="quietHoursStart")
    quiet_hours_end: time | None = Field(alias="quietHoursEnd")
    allowed_weekdays: list[int] | None = Field(alias="allowedWeekdays")
    verified_at: datetime | None = Field(alias="verifiedAt")


class NotificationChannelListResponse(BaseModel):
    meta: ResponseMeta
    items: list[NotificationChannelPublic]

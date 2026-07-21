from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.fares import ResponseMeta

ChannelType = Literal["email", "telegram", "bark", "pushplus", "webhook"]


class NotificationChannelCreateRequest(BaseModel):
    type: ChannelType
    label: Annotated[str, Field(min_length=1, max_length=120)]
    destination: Annotated[str, Field(min_length=1, max_length=2048)]


class NotificationChannelUpdateRequest(BaseModel):
    enabled: bool


class NotificationChannelPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    type: ChannelType
    label: str
    destination_masked: str = Field(alias="destinationMasked")
    enabled: bool
    verified_at: datetime | None = Field(alias="verifiedAt")


class NotificationChannelListResponse(BaseModel):
    meta: ResponseMeta
    items: list[NotificationChannelPublic]

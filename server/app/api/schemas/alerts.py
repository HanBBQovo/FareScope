from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AlertRuleType = Literal[
    "price_threshold",
    "absolute_drop",
    "percentage_drop",
    "new_low",
    "direct_available",
    "round_trip_range",
]


class AlertRuleCreateRequest(BaseModel):
    subscription_id: UUID = Field(alias="subscriptionId")
    name: Annotated[str, Field(min_length=1, max_length=160)]
    rule_type: AlertRuleType = Field(default="price_threshold", alias="ruleType")
    enabled: bool = True
    threshold_price_minor: int | None = Field(default=None, ge=0, alias="thresholdPriceMinor")
    threshold_currency: Annotated[str | None, Field(min_length=3, max_length=3)] = Field(
        default=None, alias="thresholdCurrency"
    )
    threshold_percentage: int | None = Field(
        default=None, ge=1, le=10000, alias="thresholdPercentage"
    )
    comparison_window_days: int | None = Field(
        default=None, ge=1, le=365, alias="comparisonWindowDays"
    )
    cooldown_seconds: int = Field(default=21600, ge=0, le=2_592_000, alias="cooldownSeconds")
    channel_ids: list[UUID] = Field(default_factory=list, max_length=10, alias="channelIds")
    rule_config: dict[str, object] = Field(default_factory=dict, alias="ruleConfig")

    model_config = ConfigDict(populate_by_name=True)


class AlertRuleUpdateRequest(BaseModel):
    name: Annotated[str | None, Field(min_length=1, max_length=160)] = None
    enabled: bool | None = None
    threshold_price_minor: int | None = Field(default=None, ge=0, alias="thresholdPriceMinor")
    threshold_currency: Annotated[str | None, Field(min_length=3, max_length=3)] = Field(
        default=None, alias="thresholdCurrency"
    )
    threshold_percentage: int | None = Field(
        default=None, ge=1, le=10000, alias="thresholdPercentage"
    )
    comparison_window_days: int | None = Field(
        default=None, ge=1, le=365, alias="comparisonWindowDays"
    )
    cooldown_seconds: int | None = Field(default=None, ge=0, le=2_592_000, alias="cooldownSeconds")
    channel_ids: list[UUID] | None = Field(default=None, max_length=10, alias="channelIds")
    rule_config: dict[str, object] | None = Field(default=None, alias="ruleConfig")

    model_config = ConfigDict(populate_by_name=True)


class AlertRulePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    subscription_id: UUID = Field(alias="subscriptionId")
    name: str
    rule_type: AlertRuleType = Field(alias="ruleType")
    enabled: bool
    severity: str
    threshold_price_minor: int | None = Field(alias="thresholdPriceMinor")
    threshold_currency: str | None = Field(alias="thresholdCurrency")
    threshold_percentage: int | None = Field(alias="thresholdPercentage")
    comparison_window_days: int | None = Field(alias="comparisonWindowDays")
    cooldown_seconds: int = Field(alias="cooldownSeconds")
    channel_ids: list[UUID] = Field(alias="channelIds")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AlertRuleListResponse(BaseModel):
    items: list[AlertRulePublic]


class AlertEventPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    alert_rule_id: UUID = Field(alias="alertRuleId")
    subscription_id: UUID = Field(alias="subscriptionId")
    collection_run_id: UUID | None = Field(alias="collectionRunId")
    event_type: str = Field(alias="eventType")
    severity: str
    title: str
    body: str
    event_payload: dict[str, object] = Field(alias="eventPayload")
    suppressed_at: datetime | None = Field(alias="suppressedAt")
    created_at: datetime = Field(alias="createdAt")


class AlertEventListResponse(BaseModel):
    items: list[AlertEventPublic]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class NotificationDeliveryPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    alert_event_id: UUID = Field(alias="alertEventId")
    notification_channel_id: UUID = Field(alias="notificationChannelId")
    status: Literal["pending", "sending", "succeeded", "failed", "suppressed"]
    attempt_count: int = Field(alias="attemptCount")
    next_attempt_at: datetime | None = Field(alias="nextAttemptAt")
    sent_at: datetime | None = Field(alias="sentAt")
    error_code: str | None = Field(alias="errorCode")
    error_message: str | None = Field(alias="errorMessage")
    updated_at: datetime = Field(alias="updatedAt")


class NotificationDeliveryListResponse(BaseModel):
    items: list[NotificationDeliveryPublic]

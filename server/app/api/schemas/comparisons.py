from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas.fares import PricePointPublic, ResponseMeta

TrendDays = Literal[7, 30, 90]


class ComparisonViewCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Annotated[str, Field(min_length=1, max_length=160)]
    subscription_ids: Annotated[
        list[UUID], Field(alias="subscriptionIds", min_length=2, max_length=8)
    ]
    trend_days: TrendDays = Field(default=30, alias="trendDays")
    idempotency_key: Annotated[
        str,
        Field(
            alias="idempotencyKey",
            min_length=8,
            max_length=80,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ]

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("comparison name is required")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_routes(self) -> Self:
        if len(set(self.subscription_ids)) != len(self.subscription_ids):
            raise ValueError("comparison routes must be unique")
        return self


class ComparisonViewReplaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Annotated[str, Field(min_length=1, max_length=160)]
    subscription_ids: Annotated[
        list[UUID], Field(alias="subscriptionIds", min_length=2, max_length=8)
    ]
    trend_days: TrendDays = Field(alias="trendDays")
    expected_version: Annotated[int, Field(alias="expectedVersion", ge=1)]

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("comparison name is required")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_routes(self) -> Self:
        if len(set(self.subscription_ids)) != len(self.subscription_ids):
            raise ValueError("comparison routes must be unique")
        return self


class ComparisonViewPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    name: str
    currency: str
    trend_days: TrendDays = Field(alias="trendDays")
    version: int
    configured_route_count: int = Field(alias="configuredRouteCount")
    active_route_count: int = Field(alias="activeRouteCount")
    missing_subscription_count: int = Field(alias="missingSubscriptionCount")
    comparable: bool
    subscription_ids: list[UUID] = Field(alias="subscriptionIds")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ComparisonViewListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[ComparisonViewPublic]
    next_cursor: str | None = Field(alias="nextCursor")
    as_of: datetime = Field(alias="asOf")


class ComparisonCalendarPointPublic(PricePointPublic):
    model_config = ConfigDict(populate_by_name=True)

    direct_verified: bool = Field(default=False, alias="directVerified")


class ComparisonRouteSnapshotPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subscription_id: UUID = Field(alias="subscriptionId")
    name: str
    enabled: bool
    origin: str
    destination: str
    origin_name: str = Field(alias="originName")
    destination_name: str = Field(alias="destinationName")
    trip_type: Literal["oneway", "roundtrip"] = Field(alias="tripType")
    departure_date: date = Field(alias="departureDate")
    return_date: date | None = Field(alias="returnDate")
    direct_only: bool = Field(alias="directOnly")
    currency: str
    latest_detailed_price_minor: int | None = Field(alias="latestDetailedPriceMinor")
    detailed_price_status: Literal["current", "stale", "unavailable"] = Field(
        alias="detailedPriceStatus"
    )
    detailed_observed_at: datetime | None = Field(alias="detailedObservedAt")
    period_min_price_minor: int | None = Field(alias="periodMinPriceMinor")
    period_max_price_minor: int | None = Field(alias="periodMaxPriceMinor")
    period_average_price_minor: float | None = Field(alias="periodAveragePriceMinor")
    period_sample_count: int = Field(alias="periodSampleCount")
    change_percent: float | None = Field(alias="changePercent")
    detailed_trend: list[PricePointPublic] = Field(alias="detailedTrend")
    latest_calendar_price_minor: int | None = Field(alias="latestCalendarPriceMinor")
    calendar_lowest_price_minor: int | None = Field(alias="calendarLowestPriceMinor")
    calendar_total_price_minor: int | None = Field(alias="calendarTotalPriceMinor")
    calendar_price_basis: Literal["one_way_lowest", "round_trip_total"] | None = Field(
        alias="calendarPriceBasis"
    )
    calendar_observed_at: datetime | None = Field(alias="calendarObservedAt")
    calendar_direct_verified: bool = Field(alias="calendarDirectVerified")
    calendar_trend: list[ComparisonCalendarPointPublic] = Field(alias="calendarTrend")


class ComparisonSnapshotResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    view: ComparisonViewPublic
    routes: list[ComparisonRouteSnapshotPublic]

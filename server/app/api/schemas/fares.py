from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResponseMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["live", "demo"] = "live"
    generated_at: datetime = Field(alias="generatedAt")


class FareSearchQueryPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trip_type: Literal["oneway", "roundtrip"] = Field(alias="tripType")
    origin: str
    destination: str
    departure_date: date = Field(alias="departureDate")
    return_date: date | None = Field(default=None, alias="returnDate")
    direct_only: bool = Field(alias="directOnly")
    passengers: int


class FareLegPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    direction: Literal["outbound", "inbound"]
    flight_number: str = Field(alias="flightNumber")
    airline: str
    origin: str
    destination: str
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    stops: int
    duration_minutes: int = Field(alias="durationMinutes")


class FareOfferPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    total_price_minor: int = Field(alias="totalPriceMinor")
    currency: str
    cabin: str
    legs: list[FareLegPublic]
    provider: str
    observed_at: datetime = Field(alias="observedAt")


class CollectionStatePublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    run_id: UUID | None = Field(default=None, alias="runId")
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    error_code: str | None = Field(default=None, alias="errorCode")


class FareSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    query: FareSearchQueryPublic
    offers: list[FareOfferPublic]
    total: int
    collection: CollectionStatePublic
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class PricePointPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    observed_at: datetime = Field(alias="observedAt")
    price_minor: int = Field(alias="priceMinor")
    lowest_price_minor: int | None = Field(default=None, alias="lowestPriceMinor")
    highest_price_minor: int | None = Field(default=None, alias="highestPriceMinor")
    average_price_minor: float | None = Field(default=None, alias="averagePriceMinor")
    sample_count: int = Field(default=1, alias="sampleCount")


class RoutePublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    origin: str
    destination: str
    origin_name: str = Field(alias="originName")
    destination_name: str = Field(alias="destinationName")
    trip_type: Literal["oneway", "roundtrip"] = Field(alias="tripType")
    direct_only: bool = Field(alias="directOnly")
    currency: str
    latest_price_minor: int | None = Field(alias="latestPriceMinor")
    price_status: Literal["current", "stale", "unavailable"] = Field(alias="priceStatus")
    change_percent: float | None = Field(alias="changePercent")
    observed_at: datetime | None = Field(alias="observedAt")


class PriceHistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    route: RoutePublic | None
    points: list[PricePointPublic]
    min_price_minor: int | None = Field(alias="minPriceMinor")
    max_price_minor: int | None = Field(alias="maxPriceMinor")
    average_price_minor: float | None = Field(alias="averagePriceMinor")
    sample_count: int = Field(default=0, alias="sampleCount")
    resolution: Literal["raw", "hour", "day"] = "day"
    has_more: bool = Field(default=False, alias="hasMore")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class CalendarPricePointPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    departure_date: date = Field(alias="departureDate")
    return_date: date | None = Field(default=None, alias="returnDate")
    currency: str
    lowest_price_minor: int = Field(alias="lowestPriceMinor")
    total_price_minor: int | None = Field(default=None, alias="totalPriceMinor")
    observed_at: datetime = Field(alias="observedAt")
    direct_verified: bool = Field(alias="directVerified")


class CalendarPriceResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    route: RoutePublic
    points: list[CalendarPricePointPublic]
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class CollectionRunPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    query_label: str = Field(alias="queryLabel")
    provider: str
    status: Literal["success", "running", "failed", "blocked"]
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime | None = Field(alias="finishedAt")
    observations: int
    calendar_observations: int = Field(alias="calendarObservations")
    itineraries: int
    offers: int
    attempt: int
    max_attempts: int = Field(alias="maxAttempts")
    upstream_status: str | None = Field(alias="upstreamStatus")
    warning_code: str | None = Field(alias="warningCode")
    schema_fingerprint: str | None = Field(alias="schemaFingerprint")
    diagnostics: list["CollectionDiagnosticPublic"]
    duration_ms: int | None = Field(alias="durationMs")
    error_code: str | None = Field(alias="errorCode")


class CollectionDiagnosticPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    severity: Literal["warning", "error"]
    path: str | None = None
    observed_type: str | None = Field(default=None, alias="observedType")
    retryable: bool | None = None


class CollectionHealthPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    last_success_at: datetime | None = Field(alias="lastSuccessAt")
    success_rate_24h: float | None = Field(alias="successRate24h")
    next_scheduled_at: datetime | None = Field(alias="nextScheduledAt")


class CollectionRunListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    items: list[CollectionRunPublic]
    health: CollectionHealthPublic
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class CollectionRunCountsPublic(BaseModel):
    ready: int
    retrying: int
    leased: int
    running: int
    failed_24h: int = Field(alias="failed24h")


class CollectionQueueDepthsPublic(BaseModel):
    available: bool
    collector: int | None
    default: int | None
    analysis: int | None
    notifications: int | None


class CollectionSchemaSignalPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    endpoint: str
    schema_fingerprint: str = Field(alias="schemaFingerprint")
    top_level_fields: list[str] = Field(alias="topLevelFields")
    first_seen_at: datetime = Field(alias="firstSeenAt")
    last_seen_at: datetime = Field(alias="lastSeenAt")
    occurrence_count: int = Field(alias="occurrenceCount")
    state: Literal["new", "current", "historical"]


class CollectionOperationsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    runs: CollectionRunCountsPublic
    queues: CollectionQueueDepthsPublic
    schemas: list[CollectionSchemaSignalPublic]


class DashboardStatsPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lowest_price_minor: int | None = Field(alias="lowestPriceMinor")
    price_change_percent: float | None = Field(alias="priceChangePercent")
    active_subscriptions: int = Field(alias="activeSubscriptions")
    routes_tracked: int = Field(alias="routesTracked")
    collection_success_rate: float | None = Field(alias="collectionSuccessRate")


class DashboardOverviewResponse(BaseModel):
    meta: ResponseMeta
    stats: DashboardStatsPublic
    trend: list[PricePointPublic]
    routes: list[RoutePublic]

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.search import FareSearch


class SubscriptionCreateRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    search: FareSearch
    target_price_minor: Annotated[int | None, Field(ge=0, le=2_000_000_000)] = None
    poll_interval_seconds: Annotated[int, Field(ge=1800, le=604800)] = 21600
    enabled: bool = True
    tags: Annotated[list[str], Field(max_length=20)] = []


class SubscriptionStateRequest(BaseModel):
    enabled: bool


class SearchLegPublic(BaseModel):
    position: int
    origin: str
    destination: str
    departure_date: date


class SubscriptionFiltersPublic(BaseModel):
    direct_only: bool
    airline_codes: list[str]
    departure_airports: list[str]
    arrival_airports: list[str]
    max_price_minor: int | None
    max_stops: int | None
    max_duration_minutes: int | None
    departure_minute_start: int | None
    departure_minute_end: int | None


class SubscriptionPublic(BaseModel):
    id: UUID
    name: str
    enabled: bool
    poll_interval_seconds: int
    tags: list[str]
    provider: str
    query_hash: str
    trip_type: Literal["one_way", "round_trip"]
    cabin: str
    currency: str
    adults: int
    children: int
    infants: int
    legs: list[SearchLegPublic]
    filters: SubscriptionFiltersPublic
    target_price_minor: int | None
    target_currency: str | None
    next_due_at: datetime | None
    last_collected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionPublic]
    next_cursor: str | None
    as_of: datetime

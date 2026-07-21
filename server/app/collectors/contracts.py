"""Provider-neutral records emitted by collection adapters.

The collector layer deliberately keeps persistence concerns out of these records.  A
normalizer can therefore be contract-tested against redacted provider payloads before
database models or API schemas exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

IssueSeverity = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount represented in the currency's minor unit."""

    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class LocalSchedule:
    """Provider-local schedule text plus parsed time information when available.

    Ctrip payloads do not consistently include an IANA time-zone name.  We retain the
    exact provider value and never pretend that a naive local time is UTC.
    """

    raw: str
    local_datetime: datetime
    timezone_name: str | None = None

    @property
    def has_utc_offset(self) -> bool:
        return self.local_datetime.utcoffset() is not None


@dataclass(frozen=True, slots=True)
class AirportRef:
    code: str
    name: str | None = None
    terminal: str | None = None
    timezone_name: str | None = None


@dataclass(frozen=True, slots=True)
class FlightSegment:
    leg_index: int
    segment_index: int
    flight_number: str
    departure_airport: AirportRef
    arrival_airport: AirportRef
    scheduled_departure: LocalSchedule
    scheduled_arrival: LocalSchedule
    marketing_airline_code: str | None = None
    marketing_airline_name: str | None = None
    operating_airline_code: str | None = None
    operating_flight_number: str | None = None
    duration_minutes: int | None = None
    technical_stop_count: int = 0


@dataclass(frozen=True, slots=True)
class TravelLeg:
    index: int
    segments: tuple[FlightSegment, ...]
    duration_minutes: int | None = None

    @property
    def transfer_count(self) -> int:
        return max(0, len(self.segments) - 1)

    @property
    def is_direct(self) -> bool:
        return len(self.segments) == 1 and self.segments[0].technical_stop_count == 0


@dataclass(frozen=True, slots=True)
class FareOffer:
    provider_offer_id: str | None
    total: Money
    adult_base: Money | None = None
    adult_tax: Money | None = None
    seats_remaining: int | None = None


@dataclass(frozen=True, slots=True)
class Itinerary:
    provider_itinerary_id: str
    legs: tuple[TravelLeg, ...]
    offers: tuple[FareOffer, ...]
    duration_minutes: int | None = None

    @property
    def is_direct(self) -> bool:
        return bool(self.legs) and all(leg.is_direct for leg in self.legs)


@dataclass(frozen=True, slots=True)
class CalendarFare:
    departure_date: date
    lowest: Money
    return_date: date | None = None
    total: Money | None = None


@dataclass(frozen=True, slots=True)
class ParseIssue:
    code: str
    path: str
    message: str
    severity: IssueSeverity = "warning"
    observed_type: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult[RecordT]:
    records: tuple[RecordT, ...]
    issues: tuple[ParseIssue, ...]
    schema_fingerprint: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


class ProviderAdapter(Protocol):
    provider: str

    def parse_calendar(self, payload: Mapping[str, Any]) -> ParseResult[CalendarFare]: ...

    def parse_itineraries(self, payload: Mapping[str, Any]) -> ParseResult[Itinerary]: ...

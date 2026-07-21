"""Provider-specific collection contracts and adapters."""

from app.collectors.contracts import (
    AirportRef,
    CalendarFare,
    FareOffer,
    FlightSegment,
    Itinerary,
    LocalSchedule,
    Money,
    ParseIssue,
    ParseResult,
    ProviderAdapter,
    TravelLeg,
)

__all__ = [
    "AirportRef",
    "CalendarFare",
    "FareOffer",
    "FlightSegment",
    "Itinerary",
    "LocalSchedule",
    "Money",
    "ParseIssue",
    "ParseResult",
    "ProviderAdapter",
    "TravelLeg",
]

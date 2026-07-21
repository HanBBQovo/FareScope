from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IATA_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
AIRLINE_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,3}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class TripType(StrEnum):
    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"


class CabinClass(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class SearchLeg(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: str
    destination: str
    departure_date: date

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def normalize_iata_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("airport or city code must be a string")
        code = value.strip().upper()
        if not IATA_CODE_PATTERN.fullmatch(code):
            raise ValueError("airport or city code must be a three-letter IATA code")
        return code

    @model_validator(mode="after")
    def reject_same_origin_and_destination(self) -> Self:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        return self


class PassengerMix(BaseModel):
    model_config = ConfigDict(frozen=True)

    adults: Annotated[int, Field(ge=1, le=9)] = 1
    children: Annotated[int, Field(ge=0, le=8)] = 0
    infants: Annotated[int, Field(ge=0, le=8)] = 0

    @model_validator(mode="after")
    def validate_party(self) -> Self:
        if self.adults + self.children > 9:
            raise ValueError("adults and children cannot exceed nine passengers")
        if self.infants > self.adults:
            raise ValueError("each infant must be accompanied by an adult")
        return self


class SearchFilters(BaseModel):
    model_config = ConfigDict(frozen=True)

    direct_only: bool = False
    airline_codes: tuple[str, ...] = ()
    departure_airports: tuple[str, ...] = ()
    arrival_airports: tuple[str, ...] = ()
    max_price_minor: Annotated[int | None, Field(ge=0, le=2_000_000_000)] = None
    max_stops: Annotated[int | None, Field(ge=0, le=3)] = None
    max_duration_minutes: Annotated[int | None, Field(ge=30, le=2880)] = None
    departure_minute_start: Annotated[int | None, Field(ge=0, le=1439)] = None
    departure_minute_end: Annotated[int | None, Field(ge=1, le=1440)] = None

    @field_validator("airline_codes", mode="before")
    @classmethod
    def normalize_airline_codes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        codes = tuple(sorted({str(code).strip().upper() for code in value}))
        if any(not AIRLINE_CODE_PATTERN.fullmatch(code) for code in codes):
            raise ValueError("airline codes must contain two or three letters or digits")
        return codes

    @field_validator("departure_airports", "arrival_airports", mode="before")
    @classmethod
    def normalize_airport_codes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        codes = tuple(sorted({str(code).strip().upper() for code in value}))
        if any(not IATA_CODE_PATTERN.fullmatch(code) for code in codes):
            raise ValueError("airport filters must be three-letter IATA codes")
        return codes

    @model_validator(mode="after")
    def validate_departure_window(self) -> Self:
        values = (self.departure_minute_start, self.departure_minute_end)
        if (values[0] is None) != (values[1] is None):
            raise ValueError("both departure window bounds are required")
        if values[0] is not None and values[1] is not None and values[0] >= values[1]:
            raise ValueError("departure window start must be before its end")
        return self


class FareSearch(BaseModel):
    """Provider-neutral exact-date search used for collection deduplication."""

    model_config = ConfigDict(frozen=True)

    schema_version: Annotated[int, Field(ge=1)] = 1
    provider: str = "ctrip"
    trip_type: TripType
    legs: tuple[SearchLeg, ...]
    passengers: PassengerMix = PassengerMix()
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = "CNY"
    filters: SearchFilters = SearchFilters()

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("provider is required")
        return value.strip().lower()

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("currency must be a string")
        currency = value.strip().upper()
        if not CURRENCY_PATTERN.fullmatch(currency):
            raise ValueError("currency must be a three-letter ISO code")
        return currency

    @model_validator(mode="after")
    def validate_trip_shape(self) -> Self:
        expected_legs = 1 if self.trip_type is TripType.ONE_WAY else 2
        if len(self.legs) != expected_legs:
            raise ValueError(f"{self.trip_type.value} searches require {expected_legs} leg(s)")

        if self.trip_type is TripType.ROUND_TRIP:
            outbound, inbound = self.legs
            if (outbound.origin, outbound.destination) != (
                inbound.destination,
                inbound.origin,
            ):
                raise ValueError("round-trip legs must reverse origin and destination")
            if inbound.departure_date < outbound.departure_date:
                raise ValueError("return date cannot precede departure date")
        return self

    def canonical_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["filters"] = {"direct_only": self.filters.direct_only}
        return payload

    def local_filter_payload(self) -> dict[str, object]:
        payload = self.filters.model_dump(mode="json")
        payload.pop("direct_only")
        return payload

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def query_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

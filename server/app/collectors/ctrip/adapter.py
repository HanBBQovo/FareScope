"""Normalize redacted Ctrip international fare responses.

This module parses page-generated JSON only.  It does not construct browser requests,
reuse user profiles, or attempt to evade provider access controls.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

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
    TravelLeg,
)
from app.collectors.ctrip.timezones import airport_timezone
from app.collectors.schema import schema_fingerprint

_MISSING = object()
_CURRENCY_MINOR_DIGITS = {"JPY": 0, "KRW": 0}
_CALENDAR_DATE_KEYS = ("departDate", "departureDate", "date")
_CALENDAR_PRICE_KEYS = ("price", "lowestPrice", "adultPrice")
_RETURN_DATE_KEYS = ("returnDate", "arriveDate")
_TOTAL_PRICE_KEYS = ("totalPrice", "totalAmount")
_ITINERARY_LIST_KEYS = (
    "flightItineraryList",
    "itineraryList",
    "itineraries",
)
_LEG_LIST_KEYS = ("flightSegments", "legList", "legs")
_SEGMENT_LIST_KEYS = ("flightList", "segmentList", "segments", "flights")
_OFFER_LIST_KEYS = ("priceList", "priceInfoList", "fareList", "offers")
_DOTNET_DATE_RE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


@dataclass(slots=True)
class _ParseContext:
    issues: list[ParseIssue]

    def issue(
        self,
        code: str,
        path: str,
        message: str,
        value: Any = _MISSING,
        *,
        severity: str = "warning",
    ) -> None:
        observed_type = None if value is _MISSING else type(value).__name__
        self.issues.append(
            ParseIssue(
                code=code,
                path=path,
                message=message,
                severity="error" if severity == "error" else "warning",
                observed_type=observed_type,
            )
        )


class CtripAdapter:
    """Provider adapter for verified international calendar and itinerary fields."""

    provider = "ctrip"

    def __init__(self, *, default_currency: str = "CNY") -> None:
        self.default_currency = default_currency.upper()

    def parse_calendar(self, payload: Mapping[str, Any]) -> ParseResult[CalendarFare]:
        ctx = _ParseContext(issues=[])
        candidates = list(_find_calendar_candidates(payload))
        records: list[CalendarFare] = []
        payload_currency = _find_currency(payload) or self.default_currency

        if not candidates:
            ctx.issue(
                "calendar_records_missing",
                "$",
                "No objects containing a recognized departure date and price were found",
                payload,
                severity="error",
            )

        for path, item in candidates:
            record = self._parse_calendar_item(item, path, ctx, payload_currency)
            if record is not None:
                records.append(record)

        unique_records = _deduplicate_calendar(records, ctx)
        return ParseResult(
            records=tuple(unique_records),
            issues=tuple(ctx.issues),
            schema_fingerprint=schema_fingerprint(payload),
            metadata={"provider": self.provider, "candidate_count": len(candidates)},
        )

    def parse_itineraries(self, payload: Mapping[str, Any]) -> ParseResult[Itinerary]:
        ctx = _ParseContext(issues=[])
        candidates = list(_find_named_lists(payload, _ITINERARY_LIST_KEYS))
        records: list[Itinerary] = []
        payload_currency = _find_currency(payload) or self.default_currency

        if not candidates:
            ctx.issue(
                "itinerary_list_missing",
                "$",
                "No recognized itinerary list was found",
                payload,
                severity="error",
            )

        for list_path, items in candidates:
            for index, item in enumerate(items):
                path = f"{list_path}[{index}]"
                if not isinstance(item, Mapping):
                    ctx.issue("invalid_itinerary", path, "Itinerary must be an object", item)
                    continue
                record = self._parse_itinerary(item, path, ctx, payload_currency)
                if record is not None:
                    records.append(record)

        return ParseResult(
            records=tuple(_deduplicate_itineraries(records, ctx)),
            issues=tuple(ctx.issues),
            schema_fingerprint=schema_fingerprint(payload),
            metadata={"provider": self.provider, "itinerary_list_count": len(candidates)},
        )

    def _parse_calendar_item(
        self,
        item: Mapping[str, Any],
        path: str,
        ctx: _ParseContext,
        inherited_currency: str,
    ) -> CalendarFare | None:
        raw_departure = _first(item, _CALENDAR_DATE_KEYS)
        raw_price = _first(item, _CALENDAR_PRICE_KEYS)
        departure = _parse_date(raw_departure)
        if departure is None:
            ctx.issue(
                "invalid_departure_date",
                path,
                f"Departure date is missing or invalid (shape={_redacted_shape(raw_departure)})",
                raw_departure,
            )
            return None

        currency = _read_currency(item, inherited_currency)
        lowest = _parse_money(raw_price, currency)
        if lowest is None:
            ctx.issue("invalid_price", path, "Lowest price is missing or invalid", raw_price)
            return None

        raw_return = _first(item, _RETURN_DATE_KEYS, default=None)
        return_date = _parse_date(raw_return) if raw_return not in (None, "") else None
        if (
            raw_return not in (None, "")
            and return_date is None
            and not _is_null_date_marker(raw_return)
        ):
            ctx.issue(
                "invalid_return_date",
                path,
                f"Return date is invalid (shape={_redacted_shape(raw_return)})",
                raw_return,
            )

        raw_total = _first(item, _TOTAL_PRICE_KEYS, default=None)
        total = _parse_money(raw_total, currency) if raw_total is not None else None
        if raw_total is not None and total is None:
            ctx.issue("invalid_total_price", path, "Total price is invalid", raw_total)

        return CalendarFare(
            departure_date=departure,
            return_date=return_date,
            lowest=lowest,
            total=total,
        )

    def _parse_itinerary(
        self,
        item: Mapping[str, Any],
        path: str,
        ctx: _ParseContext,
        inherited_currency: str,
    ) -> Itinerary | None:
        raw_identifier = _first(item, ("itineraryId", "routeId", "id", "sequenceId"), default=None)
        identifier = _clean_text(raw_identifier)
        if identifier is None:
            ctx.issue(
                "itinerary_id_missing", path, "Itinerary identifier is missing", raw_identifier
            )
            identifier = f"anonymous:{path}"

        leg_list = _first_sequence(item, _LEG_LIST_KEYS)
        if leg_list is None:
            ctx.issue(
                "itinerary_legs_missing",
                path,
                "Itinerary does not contain a recognized leg list",
                item,
            )
            return None

        legs: list[TravelLeg] = []
        for leg_index, raw_leg in enumerate(leg_list):
            leg_path = f"{path}.legs[{leg_index}]"
            leg = self._parse_leg(raw_leg, leg_index, leg_path, ctx)
            if leg is not None:
                legs.append(leg)
        if not legs:
            ctx.issue(
                "itinerary_has_no_valid_legs",
                path,
                "No valid travel legs were parsed",
                severity="error",
            )
            return None

        offers = self._parse_offers(item, path, ctx, inherited_currency)
        raw_duration = _first(item, ("duration", "durationMinutes", "totalDuration"), default=None)
        duration = _parse_duration_minutes(raw_duration)
        if raw_duration is not None and duration is None:
            ctx.issue(
                "invalid_itinerary_duration", path, "Itinerary duration is invalid", raw_duration
            )

        return Itinerary(
            provider_itinerary_id=identifier,
            legs=tuple(legs),
            offers=tuple(offers),
            duration_minutes=duration,
        )

    def _parse_leg(
        self,
        raw_leg: Any,
        leg_index: int,
        path: str,
        ctx: _ParseContext,
    ) -> TravelLeg | None:
        if not isinstance(raw_leg, Mapping):
            ctx.issue("invalid_leg", path, "Travel leg must be an object", raw_leg)
            return None
        segment_list = _first_sequence(raw_leg, _SEGMENT_LIST_KEYS)
        if segment_list is None:
            if _looks_like_segment(raw_leg):
                segment_list = [raw_leg]
            else:
                ctx.issue(
                    "leg_segments_missing",
                    path,
                    "Travel leg has no recognized segment list",
                    raw_leg,
                )
                return None

        segments: list[FlightSegment] = []
        for segment_index, raw_segment in enumerate(segment_list):
            segment_path = f"{path}.segments[{segment_index}]"
            segment = self._parse_segment(raw_segment, leg_index, segment_index, segment_path, ctx)
            if segment is not None:
                segments.append(segment)
        if not segments:
            return None

        raw_duration = _first(
            raw_leg, ("duration", "durationMinutes", "totalDuration"), default=None
        )
        duration = _parse_duration_minutes(raw_duration)
        if raw_duration is not None and duration is None:
            ctx.issue("invalid_leg_duration", path, "Travel leg duration is invalid", raw_duration)
        return TravelLeg(index=leg_index, segments=tuple(segments), duration_minutes=duration)

    def _parse_segment(
        self,
        raw_segment: Any,
        leg_index: int,
        segment_index: int,
        path: str,
        ctx: _ParseContext,
    ) -> FlightSegment | None:
        if not isinstance(raw_segment, Mapping):
            ctx.issue("invalid_segment", path, "Flight segment must be an object", raw_segment)
            return None

        flight_number = _clean_text(
            _first(raw_segment, ("flightNo", "flightNumber", "marketFlightNo"), default=None)
        )
        departure_code = _clean_code(
            _first(
                raw_segment,
                ("departureAirportCode", "departAirportCode", "departureAirport"),
                default=None,
            )
        )
        arrival_code = _clean_code(
            _first(
                raw_segment,
                ("arrivalAirportCode", "arriveAirportCode", "arrivalAirport"),
                default=None,
            )
        )
        departure_time = _parse_schedule(
            _first(
                raw_segment,
                ("departureDateTime", "departDateTime", "departureTime"),
                default=None,
            ),
            _clean_text(
                _first(raw_segment, ("departureTimeZone", "departTimeZone"), default=None)
            )
            or airport_timezone(departure_code),
        )
        arrival_time = _parse_schedule(
            _first(
                raw_segment,
                ("arrivalDateTime", "arriveDateTime", "arrivalTime"),
                default=None,
            ),
            _clean_text(
                _first(raw_segment, ("arrivalTimeZone", "arriveTimeZone"), default=None)
            )
            or airport_timezone(arrival_code),
        )

        missing: list[str] = []
        if flight_number is None:
            missing.append("flight number")
        if departure_code is None:
            missing.append("departure airport")
        if arrival_code is None:
            missing.append("arrival airport")
        if departure_time is None:
            missing.append("departure time")
        if arrival_time is None:
            missing.append("arrival time")
        if missing:
            ctx.issue(
                "segment_required_fields_missing",
                path,
                f"Segment is missing: {', '.join(missing)}",
                raw_segment,
            )
            return None

        raw_duration = _first(raw_segment, ("duration", "durationMinutes"), default=None)
        duration = _parse_duration_minutes(raw_duration)
        if raw_duration is not None and duration is None:
            ctx.issue("invalid_segment_duration", path, "Flight duration is invalid", raw_duration)

        stop_value = _first(raw_segment, ("stopList", "stopInfoList", "stops"), default=[])
        technical_stop_count = len(stop_value) if _is_sequence(stop_value) else 0
        if stop_value not in (None, []) and not _is_sequence(stop_value):
            ctx.issue("invalid_stop_list", path, "Technical stops must be a list", stop_value)

        assert flight_number is not None
        assert departure_code is not None
        assert arrival_code is not None
        assert departure_time is not None
        assert arrival_time is not None
        return FlightSegment(
            leg_index=leg_index,
            segment_index=segment_index,
            flight_number=flight_number,
            marketing_airline_code=_clean_code(
                _first(
                    raw_segment,
                    ("airlineCode", "marketingAirlineCode", "marketAirlineCode"),
                    default=None,
                )
            ),
            marketing_airline_name=_clean_text(
                _first(raw_segment, ("airlineName", "marketingAirlineName"), default=None)
            ),
            operating_airline_code=_clean_code(
                _first(raw_segment, ("operatingAirlineCode", "operateAirlineCode"), default=None)
            ),
            operating_flight_number=_clean_text(
                _first(raw_segment, ("operatingFlightNo", "operateFlightNo"), default=None)
            ),
            departure_airport=AirportRef(
                code=departure_code,
                name=_clean_text(
                    _first(raw_segment, ("departureAirportName", "departAirportName"), default=None)
                ),
                terminal=_clean_text(
                    _first(raw_segment, ("departureTerminal", "departTerminal"), default=None)
                ),
                timezone_name=departure_time.timezone_name,
            ),
            arrival_airport=AirportRef(
                code=arrival_code,
                name=_clean_text(
                    _first(raw_segment, ("arrivalAirportName", "arriveAirportName"), default=None)
                ),
                terminal=_clean_text(
                    _first(raw_segment, ("arrivalTerminal", "arriveTerminal"), default=None)
                ),
                timezone_name=arrival_time.timezone_name,
            ),
            scheduled_departure=departure_time,
            scheduled_arrival=arrival_time,
            duration_minutes=duration,
            technical_stop_count=technical_stop_count,
        )

    def _parse_offers(
        self,
        item: Mapping[str, Any],
        path: str,
        ctx: _ParseContext,
        inherited_currency: str,
    ) -> list[FareOffer]:
        offer_list = _first_sequence(item, _OFFER_LIST_KEYS)
        if offer_list is None:
            ctx.issue("offer_list_missing", path, "Itinerary has no recognized fare offer list")
            return []

        offers: list[FareOffer] = []
        for index, raw_offer in enumerate(offer_list):
            offer_path = f"{path}.offers[{index}]"
            if not isinstance(raw_offer, Mapping):
                ctx.issue("invalid_offer", offer_path, "Fare offer must be an object", raw_offer)
                continue
            currency = _read_currency(raw_offer, inherited_currency)
            raw_total = _first(raw_offer, _TOTAL_PRICE_KEYS, default=None)
            if raw_total is None:
                raw_total = _first(raw_offer, ("price", "adultPrice"), default=None)
            total = _parse_money(raw_total, currency)
            if total is None:
                ctx.issue(
                    "offer_total_missing",
                    offer_path,
                    "Fare offer has no valid total price",
                    raw_total,
                )
                continue
            raw_adult_base = _first(raw_offer, ("adultPrice", "adultBasePrice"), default=None)
            adult_base = _parse_money(raw_adult_base, currency)
            if raw_adult_base is not None and adult_base is None:
                ctx.issue(
                    "invalid_adult_base_price",
                    offer_path,
                    "Adult base price is invalid",
                    raw_adult_base,
                )
            raw_adult_tax = _first(raw_offer, ("adultTax", "adultTaxPrice"), default=None)
            adult_tax = _parse_money(raw_adult_tax, currency)
            if raw_adult_tax is not None and adult_tax is None:
                ctx.issue(
                    "invalid_adult_tax",
                    offer_path,
                    "Adult tax is invalid",
                    raw_adult_tax,
                )
            raw_seats = _first(raw_offer, ("seatCount", "seatsRemaining"), default=None)
            seats_remaining = _parse_non_negative_int(raw_seats)
            if raw_seats is not None and seats_remaining is None:
                ctx.issue(
                    "invalid_seat_count",
                    offer_path,
                    "Remaining seat count is invalid",
                    raw_seats,
                )
            offers.append(
                FareOffer(
                    provider_offer_id=_clean_text(
                        _first(raw_offer, ("priceId", "offerId", "id"), default=None)
                    ),
                    total=total,
                    adult_base=adult_base,
                    adult_tax=adult_tax,
                    seats_remaining=seats_remaining,
                )
            )
        return offers


def _find_calendar_candidates(
    value: Any, path: str = "$"
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        has_date = any(key in value for key in _CALENDAR_DATE_KEYS)
        has_price = any(key in value for key in (*_CALENDAR_PRICE_KEYS, *_TOTAL_PRICE_KEYS))
        if has_date and has_price:
            yield path, value
            return
        for key, item in value.items():
            yield from _find_calendar_candidates(item, f"{path}.{key}")
    elif _is_sequence(value):
        for index, item in enumerate(value):
            yield from _find_calendar_candidates(item, f"{path}[{index}]")


def _find_named_lists(
    value: Any, keys: Sequence[str], path: str = "$"
) -> Iterable[tuple[str, Sequence[Any]]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in keys and _is_sequence(item):
                yield child_path, item
            else:
                yield from _find_named_lists(item, keys, child_path)
    elif _is_sequence(value):
        for index, item in enumerate(value):
            yield from _find_named_lists(item, keys, f"{path}[{index}]")


def _first(value: Mapping[str, Any], keys: Sequence[str], *, default: Any = _MISSING) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return default


def _first_sequence(value: Mapping[str, Any], keys: Sequence[str]) -> Sequence[Any] | None:
    candidate = _first(value, keys, default=None)
    return candidate if _is_sequence(candidate) else None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _clean_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _clean_code(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = _first(value, ("code", "airportCode", "airlineCode"), default=None)
    text = _clean_text(value)
    return text.upper() if text else None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    dotnet_match = _DOTNET_DATE_RE.fullmatch(stripped)
    if dotnet_match is not None:
        try:
            milliseconds = int(dotnet_match.group(1))
            parsed_utc = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                milliseconds=milliseconds
            )
            offset = dotnet_match.group(2)
            if offset:
                sign = 1 if offset[0] == "+" else -1
                offset_minutes = int(offset[1:3]) * 60 + int(offset[3:])
                parsed_utc = parsed_utc.astimezone(
                    timezone(sign * timedelta(minutes=offset_minutes))
                )
            return parsed_utc.date()
        except (OverflowError, ValueError):
            return None

    candidate = stripped[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def _is_null_date_marker(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = _DOTNET_DATE_RE.fullmatch(value.strip())
    return match is not None and int(match.group(1)) < 0


def _redacted_shape(value: Any) -> str:
    if not isinstance(value, str):
        return type(value).__name__
    return "".join(
        "#" if character.isdigit() else "a" if character.isalpha() else character
        for character in value.strip()[:32]
    ) or "empty"


def _parse_schedule(value: Any, timezone_name: str | None) -> LocalSchedule | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    candidate = raw.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return LocalSchedule(raw=raw, local_datetime=parsed, timezone_name=timezone_name)


def _parse_duration_minutes(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        result = int(value)
        return result if result >= 0 else None
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate.isdigit():
        return int(candidate)
    hours = re.search(r"(\d+)\s*(?:h|hour|小时)", candidate)
    minutes = re.search(r"(\d+)\s*(?:m|min|minute|分钟)", candidate)
    if not hours and not minutes:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def _parse_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _read_currency(value: Mapping[str, Any], default: str) -> str:
    raw = _first(value, ("currency", "currencyCode", "priceCurrency"), default=default)
    code = _clean_code(raw)
    return code if code and len(code) == 3 else default


def _find_currency(value: Any) -> str | None:
    if isinstance(value, Mapping):
        direct = _clean_code(
            _first(value, ("currency", "currencyCode", "priceCurrency"), default=None)
        )
        if direct and len(direct) == 3:
            return direct
        for item in value.values():
            found = _find_currency(item)
            if found:
                return found
    elif _is_sequence(value):
        for item in value:
            found = _find_currency(item)
            if found:
                return found
    return None


def _parse_money(value: Any, currency: str) -> Money | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        candidate = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    else:
        candidate = str(value)
    try:
        major = Decimal(candidate)
    except (InvalidOperation, ValueError):
        return None
    if not major.is_finite() or major < 0:
        return None
    digits = _CURRENCY_MINOR_DIGITS.get(currency, 2)
    scale = Decimal(10) ** digits
    amount_minor = int((major * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return Money(amount_minor=amount_minor, currency=currency)


def _looks_like_segment(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("flightNo", "flightNumber", "marketFlightNo"))


def _deduplicate_calendar(
    records: Sequence[CalendarFare], ctx: _ParseContext
) -> list[CalendarFare]:
    unique: dict[tuple[date, date | None, str], CalendarFare] = {}
    for record in records:
        key = (record.departure_date, record.return_date, record.lowest.currency)
        current = unique.get(key)
        if current is not None:
            ctx.issue(
                "duplicate_calendar_date",
                "$",
                "Duplicate date pair found; retained the lower observed price",
            )
        if current is None or record.lowest.amount_minor < current.lowest.amount_minor:
            unique[key] = record
    return sorted(
        unique.values(), key=lambda item: (item.departure_date, item.return_date or date.min)
    )


def _deduplicate_itineraries(records: Sequence[Itinerary], ctx: _ParseContext) -> list[Itinerary]:
    unique: dict[str, Itinerary] = {}
    for record in records:
        if record.provider_itinerary_id in unique:
            ctx.issue(
                "duplicate_itinerary_id",
                "$",
                f"Duplicate itinerary id {record.provider_itinerary_id!r} was ignored",
            )
            continue
        unique[record.provider_itinerary_id] = record
    return list(unique.values())

"""Transactional persistence for provider capture payloads.

The browser runtime deliberately knows nothing about SQLAlchemy.  This module is the
boundary where provider-neutral adapter records become durable, queryable observations.
It is intentionally written around short-lived transactions: callers perform browser I/O
outside a transaction and invoke these functions only after a response capture completes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.contracts import FlightSegment, ParseIssue, ParseResult
from app.collectors.contracts import Itinerary as ParsedItinerary
from app.collectors.ctrip import CtripAdapter
from app.collectors.runtime.models import CapturedPayload
from app.collectors.schema import schema_shape
from app.models import (
    CalendarPriceObservation,
    CollectionRun,
    FareOffer,
    Itinerary,
    LatestCalendarPriceSnapshot,
    LatestPriceSnapshot,
    PriceObservation,
    Provider,
    SchemaObservation,
    SearchQuery,
    Segment,
    Subscription,
)
from app.models.enums import CollectionStatus
from app.models.mixins import utc_now


@dataclass(frozen=True, slots=True)
class CollectionPersistenceResult:
    """Counts and diagnostics emitted by one idempotent persistence attempt."""

    observed_at: datetime
    calendar_count: int = 0
    calendar_snapshot_count: int = 0
    itinerary_count: int = 0
    offer_count: int = 0
    price_observation_count: int = 0
    latest_snapshot_count: int = 0
    diagnostics: tuple[ParseIssue, ...] = ()
    schema_fingerprints: tuple[str, ...] = ()

    @property
    def has_data(self) -> bool:
        return bool(self.calendar_count or self.itinerary_count or self.offer_count)


@dataclass(frozen=True, slots=True)
class _PersistedOffer:
    itinerary_id: UUID
    fare_offer_id: UUID
    fingerprint: str
    currency: str
    total_price_minor: int
    is_direct: bool


async def persist_collection_payloads(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    search_query: SearchQuery,
    captures: Sequence[CapturedPayload],
    adapter: Any | None = None,
    observed_at: datetime | None = None,
) -> CollectionPersistenceResult:
    """Parse and persist all captures inside the caller's active transaction.

    The function never commits.  It can safely be called again with the same run and
    payload: unique keys plus conflict-safe upserts make the operation idempotent.
    """

    if not captures:
        raise ValueError("at least one captured payload is required")
    adapter = adapter or CtripAdapter(default_currency=search_query.currency)
    observation_time = _ensure_utc(observed_at or run.started_at or utc_now())

    diagnostics: list[ParseIssue] = []
    schema_fingerprints: list[str] = []
    calendar_count = 0
    calendar_snapshot_count = 0
    itinerary_count = 0
    offer_count = 0
    price_observation_count = 0
    latest_snapshot_count = 0
    persisted_offers: list[_PersistedOffer] = []

    for capture in captures:
        if capture.provider.casefold() != provider.code.casefold():
            diagnostics.append(
                ParseIssue(
                    code="provider_mismatch",
                    path=capture.capture_name,
                    message="Captured payload provider does not match the collection run",
                    severity="error",
                )
            )
            continue

        await _upsert_schema_observation(
            session,
            run=run,
            provider=provider,
            capture=capture,
            observed_at=observation_time,
        )

        if capture.capture_name == "calendar":
            parsed_calendar = adapter.parse_calendar(capture.payload)
            diagnostics.extend(parsed_calendar.issues)
            schema_fingerprints.append(parsed_calendar.schema_fingerprint)
            persisted_calendar_count, persisted_snapshot_count = await _persist_calendar(
                session,
                run=run,
                provider=provider,
                search_query=search_query,
                capture=capture,
                parsed=parsed_calendar,
                observed_at=observation_time,
            )
            calendar_count += persisted_calendar_count
            calendar_snapshot_count += persisted_snapshot_count
        elif capture.capture_name == "batch_search":
            parsed_itineraries = adapter.parse_itineraries(capture.payload)
            diagnostics.extend(parsed_itineraries.issues)
            schema_fingerprints.append(parsed_itineraries.schema_fingerprint)
            persisted, itinerary_added, offer_added, itinerary_diagnostics = (
                await _persist_itineraries(
                    session,
                    run=run,
                    provider=provider,
                    search_query=search_query,
                    parsed=parsed_itineraries,
                )
            )
            persisted_offers.extend(persisted)
            itinerary_count += itinerary_added
            offer_count += offer_added
            diagnostics.extend(itinerary_diagnostics)

    if schema_fingerprints:
        run.schema_fingerprint = _combine_fingerprints(schema_fingerprints)

    price_observation_count, latest_snapshot_count = await _persist_price_observations(
        session,
        run=run,
        provider=provider,
        search_query=search_query,
        offers=persisted_offers,
        observed_at=observation_time,
    )
    run.itinerary_count = itinerary_count
    run.offer_count = offer_count
    run.run_metadata = _merge_metadata(
        run.run_metadata,
        {
            "capture_count": len(captures),
            "calendar_count": calendar_count,
            "calendar_snapshot_count": calendar_snapshot_count,
            "itinerary_count": itinerary_count,
            "offer_count": offer_count,
            "price_observation_count": price_observation_count,
            "latest_snapshot_count": latest_snapshot_count,
            "diagnostics": [_issue_payload(issue) for issue in diagnostics[-100:]],
        },
    )
    await session.flush()
    return CollectionPersistenceResult(
        observed_at=observation_time,
        calendar_count=calendar_count,
        calendar_snapshot_count=calendar_snapshot_count,
        itinerary_count=itinerary_count,
        offer_count=offer_count,
        price_observation_count=price_observation_count,
        latest_snapshot_count=latest_snapshot_count,
        diagnostics=tuple(diagnostics),
        schema_fingerprints=tuple(schema_fingerprints),
    )


async def finalize_collection_success(
    session: AsyncSession,
    *,
    run: CollectionRun,
    search_query: SearchQuery,
    result: CollectionPersistenceResult,
    finished_at: datetime | None = None,
) -> None:
    """Mark a run successful and advance all enabled subscribers for its shared query."""

    finished = _ensure_utc(finished_at or utc_now())
    run.status = CollectionStatus.SUCCEEDED.value
    run.finished_at = finished
    run.error_code = None
    run.error_message = None
    run.run_metadata = _merge_metadata(
        run.run_metadata,
        {
            "finished_at": finished.isoformat(),
            "persistence": {
                "calendar_count": result.calendar_count,
                "calendar_snapshot_count": result.calendar_snapshot_count,
                "itinerary_count": result.itinerary_count,
                "offer_count": result.offer_count,
                "price_observation_count": result.price_observation_count,
                "latest_snapshot_count": result.latest_snapshot_count,
            },
        },
    )

    subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(Subscription.search_query_id == search_query.id)
            .with_for_update()
        )
    ).all()
    for subscription in subscriptions:
        subscription.last_collected_at = result.observed_at
        if subscription.enabled:
            subscription.next_due_at = result.observed_at + timedelta(
                seconds=subscription.poll_interval_seconds
            )
    await session.flush()


async def record_collection_failure(
    session: AsyncSession,
    *,
    run: CollectionRun,
    code: str,
    message: str,
    retryable: bool,
    diagnostics: Iterable[Mapping[str, Any]] = (),
    finished_at: datetime | None = None,
) -> None:
    """Persist a bounded, classified failure without retaining provider payloads."""

    finished = _ensure_utc(finished_at or utc_now())
    run.status = CollectionStatus.FAILED.value
    run.finished_at = finished
    run.error_code = code[:120]
    run.error_message = message[:2000]
    safe_diagnostics = [_safe_diagnostic(item) for item in diagnostics]
    run.run_metadata = _merge_metadata(
        run.run_metadata,
        {
            "failure": {
                "code": code[:120],
                "retryable": retryable,
                "diagnostics": safe_diagnostics[-100:],
            }
        },
    )
    await session.flush()


async def _upsert_schema_observation(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    capture: CapturedPayload,
    observed_at: datetime,
) -> None:
    fingerprint = _payload_schema_fingerprint(capture.payload)
    endpoint = capture.url_without_query[:160]
    summary = _bounded_schema_summary(capture.payload)
    statement = (
        pg_insert(SchemaObservation)
        .values(
            id=uuid4(),
            provider_id=provider.id,
            collection_run_id=run.id,
            endpoint=endpoint,
            schema_fingerprint=fingerprint,
            field_summary=summary,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            occurrence_count=1,
        )
        .on_conflict_do_update(
            index_elements=[
                SchemaObservation.provider_id,
                SchemaObservation.endpoint,
                SchemaObservation.schema_fingerprint,
            ],
            set_={
                "collection_run_id": run.id,
                "last_seen_at": observed_at,
                "occurrence_count": SchemaObservation.occurrence_count + 1,
                "field_summary": summary,
            },
        )
    )
    await session.execute(statement)


async def _persist_calendar(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    search_query: SearchQuery,
    capture: CapturedPayload,
    parsed: ParseResult[Any],
    observed_at: datetime,
) -> tuple[int, int]:
    rows: list[dict[str, Any]] = []
    for record in parsed.records:
        fingerprint = _fingerprint(
            {
                "departure_date": record.departure_date.isoformat(),
                "return_date": record.return_date.isoformat() if record.return_date else None,
                "currency": record.lowest.currency,
            }
        )
        rows.append(
            {
                "id": uuid4(),
                "observed_at": observed_at,
                "search_query_id": search_query.id,
                "collection_run_id": run.id,
                "provider_id": provider.id,
                "departure_date": record.departure_date,
                "return_date": record.return_date,
                "fingerprint": fingerprint,
                "currency": record.lowest.currency,
                "lowest_price_minor": record.lowest.amount_minor,
                "total_price_minor": record.total.amount_minor if record.total else None,
                "source_endpoint": capture.url_without_query[:160],
                "observation_metadata": {
                    "capture_name": capture.capture_name,
                    "schema_fingerprint": parsed.schema_fingerprint,
                },
            }
        )
    if not rows:
        return 0, 0
    statement = pg_insert(CalendarPriceObservation).values(rows).on_conflict_do_nothing(
        index_elements=[
            CalendarPriceObservation.observed_at,
            CalendarPriceObservation.collection_run_id,
            CalendarPriceObservation.fingerprint,
        ]
    )
    await session.execute(statement)
    snapshot_values_by_key: dict[tuple[UUID, Any, Any, str], dict[str, Any]] = {}
    for row in rows:
        snapshot_value = {
            "id": uuid4(),
            "search_query_id": row["search_query_id"],
            "collection_run_id": row["collection_run_id"],
            "provider_id": row["provider_id"],
            "departure_date": row["departure_date"],
            "return_date": row["return_date"],
            "currency": row["currency"],
            "lowest_price_minor": row["lowest_price_minor"],
            "total_price_minor": row["total_price_minor"],
            "observed_at": row["observed_at"],
            "source_endpoint": row["source_endpoint"],
            "direct_verified": False,
        }
        key = (
            row["search_query_id"],
            row["departure_date"],
            row["return_date"],
            row["currency"],
        )
        current = snapshot_values_by_key.get(key)
        if (
            current is None
            or snapshot_value["lowest_price_minor"] < current["lowest_price_minor"]
        ):
            snapshot_values_by_key[key] = snapshot_value
    snapshot_values = list(snapshot_values_by_key.values())
    snapshot_statement = pg_insert(LatestCalendarPriceSnapshot).values(snapshot_values)
    snapshot_statement = snapshot_statement.on_conflict_do_update(
        index_elements=[
            LatestCalendarPriceSnapshot.search_query_id,
            LatestCalendarPriceSnapshot.departure_date,
            LatestCalendarPriceSnapshot.return_date,
            LatestCalendarPriceSnapshot.currency,
        ],
        set_={
            "collection_run_id": snapshot_statement.excluded.collection_run_id,
            "provider_id": snapshot_statement.excluded.provider_id,
            "lowest_price_minor": snapshot_statement.excluded.lowest_price_minor,
            "total_price_minor": snapshot_statement.excluded.total_price_minor,
            "observed_at": snapshot_statement.excluded.observed_at,
            "source_endpoint": snapshot_statement.excluded.source_endpoint,
            "direct_verified": snapshot_statement.excluded.direct_verified,
            "updated_at": utc_now(),
        },
        where=(
            LatestCalendarPriceSnapshot.observed_at
            <= snapshot_statement.excluded.observed_at
        ),
    )
    snapshot_result = await session.execute(snapshot_statement)
    snapshot_count = (
        snapshot_result.rowcount
        if snapshot_result.rowcount is not None and snapshot_result.rowcount >= 0
        else len(snapshot_values)
    )
    return len(rows), snapshot_count


async def _persist_itineraries(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    search_query: SearchQuery,
    parsed: ParseResult[Any],
) -> tuple[list[_PersistedOffer], int, int, list[ParseIssue]]:
    persisted_offers: list[_PersistedOffer] = []
    diagnostics: list[ParseIssue] = []
    itinerary_count = 0
    offer_count = 0

    for parsed_itinerary in parsed.records:
        normalized = _normalize_itinerary(parsed_itinerary, diagnostics)
        if normalized is None:
            continue
        segment_rows, is_direct, total_duration, stop_count = normalized
        if search_query.direct_only and not is_direct:
            diagnostics.append(
                ParseIssue(
                    code="direct_only_filtered",
                    path=f"itinerary:{parsed_itinerary.provider_itinerary_id}",
                    message="Non-direct itinerary was excluded by the canonical search",
                )
            )
            continue
        itinerary_fingerprint = _fingerprint(
            {
                "provider_itinerary_id": parsed_itinerary.provider_itinerary_id,
                "segments": [
                    {
                        "leg": row["leg_position"],
                        "position": row["position"],
                        "flight": row["flight_number"],
                        "marketing": row["marketing_airline_code"],
                        "operating": row["operating_airline_code"],
                        "origin": row["origin_airport_code"],
                        "destination": row["destination_airport_code"],
                        "departure": row["departure_at_utc"].isoformat(),
                        "arrival": row["arrival_at_utc"].isoformat(),
                    }
                    for row in segment_rows
                ],
            }
        )
        itinerary_id = await _get_or_insert_itinerary(
            session,
            run=run,
            provider=provider,
            search_query=search_query,
            parsed_itinerary=parsed_itinerary,
            fingerprint=itinerary_fingerprint,
            is_direct=is_direct,
            total_duration=total_duration,
            stop_count=stop_count,
        )
        await _insert_segments_if_missing(session, itinerary_id, segment_rows)
        itinerary_count += 1

        for parsed_offer in parsed_itinerary.offers:
            offer_fingerprint = _fingerprint(
                {
                    "itinerary": itinerary_fingerprint,
                    "provider_offer_id": parsed_offer.provider_offer_id,
                    "currency": parsed_offer.total.currency,
                    "total": parsed_offer.total.amount_minor,
                    "adult_base": (
                        parsed_offer.adult_base.amount_minor if parsed_offer.adult_base else None
                    ),
                    "adult_tax": (
                        parsed_offer.adult_tax.amount_minor if parsed_offer.adult_tax else None
                    ),
                    "seats": parsed_offer.seats_remaining,
                }
            )
            offer_id = await _get_or_insert_offer(
                session,
                run=run,
                itinerary_id=itinerary_id,
                parsed_offer=parsed_offer,
                fingerprint=offer_fingerprint,
                cabin=search_query.cabin,
            )
            persisted_offers.append(
                _PersistedOffer(
                    itinerary_id=itinerary_id,
                    fare_offer_id=offer_id,
                    fingerprint=offer_fingerprint,
                    currency=parsed_offer.total.currency,
                    total_price_minor=parsed_offer.total.amount_minor,
                    is_direct=is_direct,
                )
            )
            offer_count += 1
    return persisted_offers, itinerary_count, offer_count, diagnostics


async def _get_or_insert_itinerary(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    search_query: SearchQuery,
    parsed_itinerary: ParsedItinerary,
    fingerprint: str,
    is_direct: bool,
    total_duration: int,
    stop_count: int,
) -> UUID:
    statement = (
        pg_insert(Itinerary)
        .values(
            id=uuid4(),
            collection_run_id=run.id,
            search_query_id=search_query.id,
            provider_id=provider.id,
            provider_itinerary_id=parsed_itinerary.provider_itinerary_id,
            fingerprint=fingerprint,
            total_duration_minutes=total_duration,
            stop_count=stop_count,
            is_direct=is_direct,
            leg_count=len(parsed_itinerary.legs),
            itinerary_metadata={"provider": provider.code},
        )
        .on_conflict_do_nothing(index_elements=[Itinerary.collection_run_id, Itinerary.fingerprint])
        .returning(Itinerary.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id
    existing = await session.scalar(
        select(Itinerary.id).where(
            Itinerary.collection_run_id == run.id,
            Itinerary.fingerprint == fingerprint,
        )
    )
    if existing is None:
        raise RuntimeError("itinerary upsert completed without a visible row")
    return existing


async def _insert_segments_if_missing(
    session: AsyncSession,
    itinerary_id: UUID,
    rows: Sequence[dict[str, Any]],
) -> None:
    if not rows:
        return
    values = [{"id": uuid4(), "itinerary_id": itinerary_id, **row} for row in rows]
    statement = pg_insert(Segment).values(values).on_conflict_do_nothing(
        index_elements=[Segment.itinerary_id, Segment.position]
    )
    await session.execute(statement)


async def _get_or_insert_offer(
    session: AsyncSession,
    *,
    run: CollectionRun,
    itinerary_id: UUID,
    parsed_offer: Any,
    fingerprint: str,
    cabin: str,
) -> UUID:
    statement = (
        pg_insert(FareOffer)
        .values(
            id=uuid4(),
            collection_run_id=run.id,
            itinerary_id=itinerary_id,
            provider_offer_id=parsed_offer.provider_offer_id,
            fingerprint=fingerprint,
            cabin=cabin,
            currency=parsed_offer.total.currency,
            total_price_minor=parsed_offer.total.amount_minor,
            base_price_minor=(
                parsed_offer.adult_base.amount_minor if parsed_offer.adult_base else None
            ),
            tax_minor=parsed_offer.adult_tax.amount_minor if parsed_offer.adult_tax else None,
            seats_remaining=parsed_offer.seats_remaining,
            offer_metadata={"provider_offer_id": parsed_offer.provider_offer_id},
        )
        .on_conflict_do_nothing(
            index_elements=[
                FareOffer.collection_run_id,
                FareOffer.itinerary_id,
                FareOffer.fingerprint,
            ]
        )
        .returning(FareOffer.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id
    existing = await session.scalar(
        select(FareOffer.id).where(
            FareOffer.collection_run_id == run.id,
            FareOffer.itinerary_id == itinerary_id,
            FareOffer.fingerprint == fingerprint,
        )
    )
    if existing is None:
        raise RuntimeError("fare offer upsert completed without a visible row")
    return existing


async def _persist_price_observations(
    session: AsyncSession,
    *,
    run: CollectionRun,
    provider: Provider,
    search_query: SearchQuery,
    offers: Sequence[_PersistedOffer],
    observed_at: datetime,
) -> tuple[int, int]:
    if not offers:
        return 0, 0
    minima: dict[tuple[str, bool], int] = {}
    for offer in offers:
        key = (offer.currency, offer.is_direct)
        minima[key] = min(minima.get(key, offer.total_price_minor), offer.total_price_minor)

    observation_rows = [
        {
            "id": uuid4(),
            "observed_at": observed_at,
            "search_query_id": search_query.id,
            "collection_run_id": run.id,
            "itinerary_id": offer.itinerary_id,
            "fare_offer_id": offer.fare_offer_id,
            "provider_id": provider.id,
            "offer_fingerprint": offer.fingerprint,
            "currency": offer.currency,
            "total_price_minor": offer.total_price_minor,
            "is_lowest": offer.total_price_minor == minima[(offer.currency, offer.is_direct)],
            "is_direct": offer.is_direct,
        }
        for offer in offers
    ]
    statement = pg_insert(PriceObservation).values(observation_rows).on_conflict_do_nothing(
        index_elements=[
            PriceObservation.observed_at,
            PriceObservation.collection_run_id,
            PriceObservation.offer_fingerprint,
        ]
    )
    result = await session.execute(statement)
    observation_count = (
        result.rowcount
        if result.rowcount is not None and result.rowcount >= 0
        else len(offers)
    )

    snapshot_rows = [
        min(
            (offer for offer in offers if (offer.currency, offer.is_direct) == key),
            key=lambda offer: (offer.total_price_minor, offer.fingerprint),
        )
        for key in sorted(minima)
    ]
    snapshot_count = 0
    for offer in snapshot_rows:
        values = {
            "id": uuid4(),
            "search_query_id": search_query.id,
            "provider_id": provider.id,
            "collection_run_id": run.id,
            "itinerary_id": offer.itinerary_id,
            "fare_offer_id": offer.fare_offer_id,
            "observed_at": observed_at,
            "currency": offer.currency,
            "total_price_minor": offer.total_price_minor,
            "is_direct": offer.is_direct,
        }
        statement = pg_insert(LatestPriceSnapshot).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                LatestPriceSnapshot.search_query_id,
                LatestPriceSnapshot.currency,
                LatestPriceSnapshot.is_direct,
            ],
            set_={
                "collection_run_id": statement.excluded.collection_run_id,
                "provider_id": statement.excluded.provider_id,
                "itinerary_id": statement.excluded.itinerary_id,
                "fare_offer_id": statement.excluded.fare_offer_id,
                "observed_at": statement.excluded.observed_at,
                "total_price_minor": statement.excluded.total_price_minor,
                "updated_at": utc_now(),
            },
            where=LatestPriceSnapshot.observed_at <= statement.excluded.observed_at,
        )
        result = await session.execute(statement)
        if result.rowcount is None or result.rowcount > 0:
            snapshot_count += 1
    return observation_count, snapshot_count


def _normalize_itinerary(
    itinerary: ParsedItinerary,
    diagnostics: list[ParseIssue],
) -> tuple[list[dict[str, Any]], bool, int, int] | None:
    segment_rows: list[dict[str, Any]] = []
    valid_leg_count = 0
    total_duration = 0
    stop_count = 0
    for leg in itinerary.legs:
        leg_rows: list[dict[str, Any]] = []
        for segment in leg.segments:
            normalized = _normalize_segment(segment, diagnostics)
            if normalized is not None:
                leg_rows.append(normalized)
        if not leg_rows:
            diagnostics.append(
                ParseIssue(
                    code="itinerary_leg_skipped",
                    path=f"itinerary:{itinerary.provider_itinerary_id}.legs[{leg.index}]",
                    message="Travel leg had no segment with a trustworthy UTC conversion",
                )
            )
            continue
        valid_leg_count += 1
        stop_count += max(0, len(leg_rows) - 1) + sum(
            int(row["segment_metadata"]["technical_stop_count"] or 0)
            for row in leg_rows
        )
        total_duration += sum(row["duration_minutes"] for row in leg_rows)
        for row in leg_rows:
            row["leg_position"] = leg.index
            row["position"] = len(segment_rows)
            segment_rows.append(row)

    if valid_leg_count != len(itinerary.legs) or not segment_rows:
        diagnostics.append(
            ParseIssue(
                code="itinerary_skipped_missing_timezone",
                path=f"itinerary:{itinerary.provider_itinerary_id}",
                message=(
                    "Itinerary was not persisted because one or more segments lack an IANA "
                    "timezone or explicit UTC offset"
                ),
            )
        )
        return None

    is_direct = all(
        row["segment_metadata"]["technical_stop_count"] == 0
        for row in segment_rows
    ) and all(
        sum(1 for row in segment_rows if row["leg_position"] == leg.index) == 1
        for leg in itinerary.legs
    )
    duration = (
        itinerary.duration_minutes
        if itinerary.duration_minutes and itinerary.duration_minutes > 0
        else total_duration
    )
    if duration <= 0:
        diagnostics.append(
            ParseIssue(
                code="itinerary_duration_invalid",
                path=f"itinerary:{itinerary.provider_itinerary_id}",
                message="Itinerary has no positive duration after normalization",
            )
        )
        return None
    return segment_rows, is_direct, duration, stop_count


def _normalize_segment(
    segment: FlightSegment,
    diagnostics: list[ParseIssue],
) -> dict[str, Any] | None:
    departure = _schedule_to_utc(segment, departure=True, diagnostics=diagnostics)
    arrival = _schedule_to_utc(segment, departure=False, diagnostics=diagnostics)
    if departure is None or arrival is None:
        return None
    departure_utc, departure_zone = departure
    arrival_utc, arrival_zone = arrival
    duration = segment.duration_minutes
    if duration is None:
        duration = int(round((arrival_utc - departure_utc).total_seconds() / 60))
    if duration <= 0:
        diagnostics.append(
            ParseIssue(
                code="segment_duration_invalid",
                path=f"segment:{segment.flight_number}",
                message="Segment duration is not positive after UTC normalization",
            )
        )
        return None
    airline = segment.marketing_airline_code or segment.operating_airline_code
    if not airline:
        diagnostics.append(
            ParseIssue(
                code="segment_airline_missing",
                path=f"segment:{segment.flight_number}",
                message="Segment has no marketing or operating airline code",
            )
        )
        return None
    return {
        "marketing_airline_code": airline,
        "operating_airline_code": segment.operating_airline_code,
        "flight_number": segment.flight_number,
        "origin_airport_code": segment.departure_airport.code,
        "destination_airport_code": segment.arrival_airport.code,
        "departure_at_utc": departure_utc,
        "arrival_at_utc": arrival_utc,
        "departure_local": segment.scheduled_departure.local_datetime.replace(tzinfo=None),
        "arrival_local": segment.scheduled_arrival.local_datetime.replace(tzinfo=None),
        "departure_timezone": departure_zone,
        "arrival_timezone": arrival_zone,
        "duration_minutes": duration,
        "aircraft_code": None,
        "segment_metadata": {
            "departure_airport_name": segment.departure_airport.name,
            "arrival_airport_name": segment.arrival_airport.name,
            "departure_terminal": segment.departure_airport.terminal,
            "arrival_terminal": segment.arrival_airport.terminal,
            "marketing_airline_name": segment.marketing_airline_name,
            "operating_flight_number": segment.operating_flight_number,
            "technical_stop_count": segment.technical_stop_count,
        },
    }


def _schedule_to_utc(
    segment: FlightSegment,
    *,
    departure: bool,
    diagnostics: list[ParseIssue],
) -> tuple[datetime, str] | None:
    schedule = segment.scheduled_departure if departure else segment.scheduled_arrival
    label = "departure" if departure else "arrival"
    local = schedule.local_datetime
    if local.tzinfo is None:
        if not schedule.timezone_name:
            diagnostics.append(
                ParseIssue(
                    code="segment_timezone_missing",
                    path=f"segment:{segment.flight_number}.{label}",
                    message="Naive provider time has neither IANA timezone nor UTC offset",
                )
            )
            return None
        try:
            timezone = ZoneInfo(schedule.timezone_name)
        except ZoneInfoNotFoundError:
            diagnostics.append(
                ParseIssue(
                    code="segment_timezone_invalid",
                    path=f"segment:{segment.flight_number}.{label}",
                    message="Provider timezone is not a known IANA zone",
                )
            )
            return None
        local = local.replace(tzinfo=timezone)
        timezone_label = schedule.timezone_name
    else:
        timezone_label = schedule.timezone_name or _offset_label(local)
    offset = local.utcoffset()
    if offset is None:
        diagnostics.append(
            ParseIssue(
                code="segment_utc_offset_missing",
                path=f"segment:{segment.flight_number}.{label}",
                message="Provider time could not produce a UTC offset",
            )
        )
        return None
    return local.astimezone(UTC), timezone_label


def _offset_label(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return "unknown"
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _payload_schema_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        schema_shape(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _bounded_schema_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    shape = schema_shape(payload)
    encoded = json.dumps(shape, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= 32_000:
        return {"shape": shape}
    return {
        "shape_truncated": True,
        "top_level": {str(key): type(value).__name__ for key, value in payload.items()},
    }


def _combine_fingerprints(fingerprints: Iterable[str]) -> str:
    return _fingerprint(sorted(set(fingerprints)))


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collection timestamps must include an explicit UTC offset")
    return value.astimezone(UTC)


def _merge_metadata(
    existing: Mapping[str, Any] | None,
    update: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(update)
    return merged


def _issue_payload(issue: ParseIssue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "path": issue.path[:240],
        "message": issue.message[:500],
        "severity": issue.severity,
        "observed_type": issue.observed_type,
    }


def _safe_diagnostic(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"kind", "code", "message", "capture_name", "status_code", "retryable"}
    return {key: str(value[key])[:500] for key in allowed if key in value}

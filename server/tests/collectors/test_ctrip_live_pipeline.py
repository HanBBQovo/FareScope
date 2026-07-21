from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    CalendarPriceObservation,
    CollectionArtifact,
    CollectionRun,
    FareOffer,
    Itinerary,
    PriceObservation,
    Provider,
    SearchLeg,
    SearchQuery,
    Segment,
)
from app.models.enums import CollectionStatus
from app.settings import Settings
from app.tasks.collection import run_collection_once

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")
RUN_LIVE = os.getenv("FARESCOPE_RUN_LIVE_CTRIP") == "1"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None or not RUN_LIVE,
        reason="requires a migrated test database and FARESCOPE_RUN_LIVE_CTRIP=1",
    ),
]


async def test_live_one_way_page_persists_available_prices_without_raw_artifacts() -> None:
    """Opt-in live proof that distinguishes detailed fares from a partial response."""

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    scheduled_at = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                provider = await session.scalar(
                    select(Provider).where(Provider.code == "ctrip")
                )
                assert provider is not None
                query = SearchQuery(
                    provider="ctrip",
                    query_hash=uuid4().hex + uuid4().hex,
                    trip_type="one_way",
                    adults=1,
                    children=0,
                    infants=0,
                    cabin="economy",
                    currency="CNY",
                    direct_only=False,
                    normalized_query={"live_verification": "SHA-TYO"},
                )
                session.add(query)
                await session.flush()
                session.add(
                    SearchLeg(
                        search_query_id=query.id,
                        position=0,
                        origin_code="SHA",
                        destination_code="TYO",
                        departure_date=date(2026, 8, 15),
                    )
                )
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"live-proof:{uuid4()}",
                    status=CollectionStatus.PENDING.value,
                    attempt=0,
                    max_attempts=3,
                    scheduled_at=scheduled_at,
                    run_metadata={"trigger": "opt_in_live_test"},
                )
                session.add(run)
                await session.flush()
                run_id = run.id

            result = await run_collection_once(
                run_id,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                ),
                session_factory=factory,
                worker_id="live-proof",
            )

            async with factory() as session:
                persisted_run = await session.get(CollectionRun, run_id)
                assert persisted_run is not None
                safe_failure = {
                    "result": result,
                    "error_code": persisted_run.error_code,
                    "upstream_status": persisted_run.upstream_status,
                    "diagnostics": persisted_run.run_metadata.get("diagnostics", []),
                }
            assert result["calendar_count"] > 0
            async with factory() as session:
                assert await _count(session, CalendarPriceObservation, run_id) > 0
                assert await _count(session, CollectionArtifact, run_id) == 0
                if result["itinerary_count"] > 0:
                    assert result["status"] == CollectionStatus.SUCCEEDED.value, safe_failure
                    assert result["offer_count"] > 0
                    assert await _count(session, FareOffer, run_id) > 0
                    assert await _count(session, PriceObservation, run_id) > 0
                else:
                    assert result["status"] == CollectionStatus.PENDING.value, safe_failure
                    assert result["retry_scheduled_at"] is not None
                    assert persisted_run.upstream_status == "partial_fare_data"
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_live_round_trip_page_preserves_calendar_and_available_details() -> None:
    """Opt-in proof that never turns a calendar-only response into a detailed quote."""

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    scheduled_at = datetime.now(UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                provider = await session.scalar(
                    select(Provider).where(Provider.code == "ctrip")
                )
                assert provider is not None
                query = SearchQuery(
                    provider="ctrip",
                    query_hash=uuid4().hex + uuid4().hex,
                    trip_type="round_trip",
                    adults=1,
                    children=0,
                    infants=0,
                    cabin="economy",
                    currency="CNY",
                    direct_only=False,
                    normalized_query={"live_verification": "NKG-BJS-round"},
                )
                session.add(query)
                await session.flush()
                session.add_all(
                    (
                        SearchLeg(
                            search_query_id=query.id,
                            position=0,
                            origin_code="NKG",
                            destination_code="BJS",
                            departure_date=date(2026, 8, 15),
                        ),
                        SearchLeg(
                            search_query_id=query.id,
                            position=1,
                            origin_code="BJS",
                            destination_code="NKG",
                            departure_date=date(2026, 8, 22),
                        ),
                    )
                )
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"live-round-proof:{uuid4()}",
                    status=CollectionStatus.PENDING.value,
                    attempt=0,
                    max_attempts=3,
                    scheduled_at=scheduled_at,
                    run_metadata={"trigger": "opt_in_live_round_test"},
                )
                session.add(run)
                await session.flush()
                run_id = run.id

            result = await run_collection_once(
                run_id,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                ),
                session_factory=factory,
                worker_id="live-round-proof",
            )

            async with factory() as session:
                persisted_run = await session.get(CollectionRun, run_id)
                assert persisted_run is not None
                safe_failure = {
                    "result": result,
                    "error_code": persisted_run.error_code,
                    "upstream_status": persisted_run.upstream_status,
                    "diagnostics": persisted_run.run_metadata.get("diagnostics", []),
                }
            assert result["calendar_count"] > 0
            async with factory() as session:
                assert await _count(session, CollectionArtifact, run_id) == 0
                if result["itinerary_count"] == 0:
                    assert result["status"] == CollectionStatus.PENDING.value, safe_failure
                    assert result["retry_scheduled_at"] is not None
                    assert persisted_run.upstream_status == "partial_fare_data"
                else:
                    assert result["status"] == CollectionStatus.SUCCEEDED.value, safe_failure
                    assert result["offer_count"] > 0, safe_failure
                    assert result["price_observation_count"] > 0, safe_failure
                    itinerary = await session.scalar(
                        select(Itinerary).where(Itinerary.collection_run_id == run_id).limit(1)
                    )
                    assert itinerary is not None
                    assert itinerary.leg_count == 2
                    leg_positions = set(
                        (
                            await session.scalars(
                                select(Segment.leg_position).where(
                                    Segment.itinerary_id == itinerary.id
                                )
                            )
                        ).all()
                    )
                    assert leg_positions == {0, 1}
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _count(session: AsyncSession, model: type[Any], run_id: UUID) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.collection_run_id == run_id)
    )
    return int(value or 0)

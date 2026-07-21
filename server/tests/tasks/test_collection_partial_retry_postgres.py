from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.runtime import BrowserRunConfig, BrowserRunResult, CaptureRule
from app.collectors.runtime.models import CapturedPayload
from app.models import (
    CalendarPriceObservation,
    CollectionArtifact,
    CollectionRun,
    FareOffer,
    Provider,
    SearchLeg,
    SearchQuery,
)
from app.models.enums import CollectionStatus
from app.settings import Settings
from app.tasks.collection import run_collection_once

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_calendar_only_capture_is_persisted_and_retried() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    scheduled_at = datetime.now(UTC).replace(microsecond=0)
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
                    normalized_query={"test": "calendar-only-retry"},
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
                    idempotency_key=f"calendar-only:{uuid4()}",
                    status=CollectionStatus.PENDING.value,
                    attempt=0,
                    max_attempts=3,
                    scheduled_at=scheduled_at,
                    run_metadata={"trigger": "test"},
                )
                session.add(run)
                await session.flush()
                run_id = run.id

            result = await run_collection_once(
                run_id,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                    collection_retry_base_seconds=5,
                    collection_retry_max_seconds=30,
                ),
                session_factory=factory,
                runner=_CalendarOnlyRunner(),  # type: ignore[arg-type]
                worker_id="partial-data-test",
            )

            assert result["status"] == CollectionStatus.PENDING.value
            assert result["calendar_count"] == 1
            assert result["itinerary_count"] == 0
            assert result["price_observation_count"] == 0
            assert result["retry_scheduled_at"] is not None

            async with factory() as session:
                persisted = await session.get(CollectionRun, run_id)
                assert persisted is not None
                assert persisted.status == CollectionStatus.PENDING.value
                assert persisted.upstream_status == "partial_fare_data"
                assert persisted.error_code == "partial_fare_data"
                assert persisted.attempt == 1
                assert persisted.scheduled_at > scheduled_at
                assert persisted.run_metadata["partial_data"]["retryable"] is True
                assert await _count(session, CalendarPriceObservation, run_id) == 1
                assert await _count(session, FareOffer, run_id) == 0
                assert await _count(session, CollectionArtifact, run_id) == 0
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _count(session, model, run_id) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.collection_run_id == run_id)
    )
    return int(value or 0)


class _CalendarOnlyRunner:
    async def run(
        self,
        config: BrowserRunConfig,
        *,
        capture_rules: tuple[CaptureRule, ...],
    ) -> BrowserRunResult:
        assert capture_rules
        finished_at = datetime.now(UTC)
        captures = (
            CapturedPayload(
                provider="ctrip",
                route_key=config.route_key,
                capture_name="calendar",
                status_code=200,
                url_without_query=(
                    "https://flights.ctrip.com/international/"
                    "search/api/lowprice/FlightIntlAndInlandLowestPriceSearch"
                ),
                received_at=finished_at,
                payload={
                    "priceList": [
                        {
                            "departDate": "2026-08-15",
                            "price": 2180,
                        }
                    ]
                },
            ),
            CapturedPayload(
                provider="ctrip",
                route_key=config.route_key,
                capture_name="batch_search",
                status_code=200,
                url_without_query=(
                    "https://flights.ctrip.com/international/search/api/search/batchSearch"
                ),
                received_at=finished_at,
                payload={
                    "status": 0,
                    "data": {
                        "bestChoiceFlightsForceTop": False,
                        "lgn": False,
                        "needUserLogin": False,
                    },
                },
            ),
        )
        return BrowserRunResult(
            provider=config.provider,
            route_key=config.route_key,
            started_at=finished_at - timedelta(seconds=1),
            finished_at=finished_at,
            captures=captures,
            diagnostics=(),
            expected_capture_names=config.expected_capture_names,
        )

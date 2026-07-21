from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.partitions import price_observation_partition_ddl
from app.models import (
    DailyTrendAggregate,
    DailyTrendAggregateCoverage,
    SearchQuery,
    Subscription,
    User,
)
from app.services.daily_trends import (
    DailyTrendSourceUnavailableError,
    maintain_daily_trend_aggregates,
)

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_detached_archive_overlap_cannot_erase_existing_daily_trend() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    partition_date = date(2097, 1, 1)
    partition_name = "price_observations_y2097m01"
    observed_at = datetime(2097, 1, 5, 8, tzinfo=UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            token = uuid4().hex
            user = User(
                username=f"trend-archive-{token}",
                normalized_username=f"trend-archive-{token}",
                display_name="Trend archive test",
                role="member",
                status="active",
            )
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
                normalized_query={"archive_safety_test": True},
            )
            session.add_all([user, query])
            await session.flush()
            session.add_all(
                [
                    Subscription(
                        user_id=user.id,
                        search_query_id=query.id,
                        name="Archived trend",
                        enabled=True,
                        poll_interval_seconds=900,
                        tags=[],
                    ),
                    DailyTrendAggregate(
                        search_query_id=query.id,
                        observation_date=observed_at.date(),
                        currency="CNY",
                        direct_only=False,
                        lowest_price_minor=120_000,
                        highest_price_minor=180_000,
                        price_sum_minor=300_000,
                        sample_count=2,
                        first_observed_at=observed_at,
                        last_observed_at=observed_at,
                    ),
                    DailyTrendAggregateCoverage(
                        search_query_id=query.id,
                        observation_date=observed_at.date(),
                        source_last_observed_at=observed_at,
                    ),
                ]
            )
            await session.flush()

            await connection.execute(text(price_observation_partition_ddl(partition_date)))
            await connection.execute(
                text(
                    "ALTER TABLE public.price_observations "
                    f"DETACH PARTITION public.{partition_name}"
                )
            )
            await connection.execute(
                text(f"ALTER TABLE public.{partition_name} SET SCHEMA farescope_archive")
            )

            with pytest.raises(DailyTrendSourceUnavailableError) as captured:
                await maintain_daily_trend_aggregates(
                    session,
                    start_date=date(2097, 1, 1),
                    end_date=date(2097, 1, 31),
                    search_query_id=query.id,
                )

            assert captured.value.archived_partitions == (partition_name,)
            aggregate = await session.scalar(
                select(DailyTrendAggregate).where(
                    DailyTrendAggregate.search_query_id == query.id,
                    DailyTrendAggregate.observation_date == observed_at.date(),
                    DailyTrendAggregate.currency == "CNY",
                    DailyTrendAggregate.direct_only.is_(False),
                )
            )
            coverage = await session.get(
                DailyTrendAggregateCoverage,
                (query.id, observed_at.date()),
            )
            assert aggregate is not None
            assert aggregate.price_sum_minor == 300_000
            assert aggregate.sample_count == 2
            assert coverage is not None
            assert coverage.source_last_observed_at == observed_at
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()

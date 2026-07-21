from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.domain.search import FareSearch, SearchFilters, SearchLeg, TripType
from app.models import AlertRule, SubscriptionFilter, User
from app.services.subscriptions import create_subscription

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_subscription_target_does_not_hide_prices_above_the_alert_threshold() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            token = uuid4().hex
            user = User(
                username=f"subscription-{token}",
                normalized_username=f"subscription-{token}",
                display_name=f"subscription-{token}",
                role="member",
                status="active",
            )
            session.add(user)
            await session.flush()

            view = await create_subscription(
                session,
                user=user,
                name="Independent target and filter",
                search=FareSearch(
                    trip_type=TripType.ONE_WAY,
                    legs=(
                        SearchLeg(
                            origin="SHA",
                            destination="TYO",
                            departure_date=date(2026, 9, 10),
                        ),
                    ),
                    filters=SearchFilters(max_price_minor=450_000),
                ),
                target_price_minor=300_000,
                poll_interval_seconds=1800,
                enabled=True,
                tags=[],
            )

            stored_filter = await session.scalar(
                select(SubscriptionFilter).where(
                    SubscriptionFilter.subscription_id == view.subscription.id
                )
            )
            rule = await session.scalar(
                select(AlertRule).where(
                    AlertRule.subscription_id == view.subscription.id,
                    AlertRule.rule_type == "price_threshold",
                )
            )

            assert stored_filter is not None
            assert stored_filter.max_price_minor == 450_000
            assert rule is not None
            assert rule.threshold_price_minor == 300_000
            assert view.target_price_minor == 300_000
            assert view.target_currency == "CNY"
        finally:
            await session.close()
            await transaction.rollback()
            await engine.dispose()

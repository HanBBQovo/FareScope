from __future__ import annotations

import os
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import (
    AlertEvent,
    AlertRule,
    AlertRuleChannel,
    CollectionRun,
    FareOffer,
    Itinerary,
    NotificationChannel,
    NotificationDelivery,
    PriceObservation,
    Provider,
    SearchQuery,
    Segment,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.models.enums import CollectionStatus
from app.security import SecretBox
from app.services.alerts import evaluate_collection_run
from app.services.notification_delivery import (
    DeliveryResult,
    claim_pending_deliveries,
    finish_delivery,
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


async def test_successful_run_creates_deduplicated_alert_and_delivery() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    observed_at = datetime(2026, 7, 20, 15, tzinfo=UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            provider = await session.scalar(select(Provider).where(Provider.code == "ctrip"))
            assert provider is not None
            suffix = uuid4().hex
            user = User(
                username=f"alerts-{suffix}",
                normalized_username=f"alerts-{suffix}",
                email=f"alerts-{suffix}@example.test",
                display_name=f"alerts-{suffix}",
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
                normalized_query={"label": "SHA-TYO"},
            )
            session.add_all([user, query])
            await session.flush()
            subscription = Subscription(
                user_id=user.id,
                search_query_id=query.id,
                name="Alert route",
                enabled=True,
                poll_interval_seconds=1800,
                next_due_at=observed_at,
                tags=[],
            )
            run = CollectionRun(
                search_query_id=query.id,
                provider_id=provider.id,
                idempotency_key=f"alert-run:{suffix}",
                status=CollectionStatus.SUCCEEDED.value,
                attempt=1,
                max_attempts=3,
                scheduled_at=observed_at,
                started_at=observed_at,
                finished_at=observed_at,
                run_metadata={},
            )
            session.add_all([subscription, run])
            await session.flush()
            session.add(
                SubscriptionFilter(
                    subscription_id=subscription.id,
                    airline_codes=["ZZ"],
                    origin_airport_codes=[],
                    destination_airport_codes=[],
                    max_price_minor=15_000,
                    currency="CNY",
                )
            )
            secret_box = SecretBox(Fernet.generate_key().decode())
            channel = NotificationChannel(
                user_id=user.id,
                name="Webhook",
                channel_type="webhook",
                enabled=True,
                secret_ciphertext=secret_box.encrypt_mapping(
                    {"destination": "https://hooks.example.test/fare"}
                ),
                config_redacted={"destination_masked": "https://hooks.example.test/***"},
            )
            session.add(channel)
            await session.flush()
            rule = AlertRule(
                user_id=user.id,
                subscription_id=subscription.id,
                name="Under target",
                rule_type="price_threshold",
                enabled=True,
                threshold_price_minor=15_000,
                threshold_currency="CNY",
                cooldown_seconds=0,
                rule_config={},
            )
            session.add(rule)
            await session.flush()
            session.add(
                AlertRuleChannel(alert_rule_id=rule.id, notification_channel_id=channel.id)
            )
            itinerary = Itinerary(
                collection_run_id=run.id,
                search_query_id=query.id,
                provider_id=provider.id,
                provider_itinerary_id="alert-itinerary",
                fingerprint=f"itinerary-{suffix}",
                total_duration_minutes=180,
                stop_count=0,
                is_direct=True,
                leg_count=1,
                itinerary_metadata={},
            )
            session.add(itinerary)
            await session.flush()
            session.add(
                Segment(
                    itinerary_id=itinerary.id,
                    position=0,
                    leg_position=0,
                    marketing_airline_code="ZZ",
                    flight_number="ZZ101",
                    origin_airport_code="PVG",
                    destination_airport_code="NRT",
                    departure_at_utc=observed_at,
                    arrival_at_utc=observed_at,
                    departure_local=observed_at.replace(tzinfo=None),
                    arrival_local=observed_at.replace(tzinfo=None),
                    departure_timezone="Asia/Shanghai",
                    arrival_timezone="Asia/Tokyo",
                    duration_minutes=180,
                    segment_metadata={},
                )
            )
            offer = FareOffer(
                collection_run_id=run.id,
                itinerary_id=itinerary.id,
                provider_offer_id="alert-offer",
                fingerprint=f"offer-{suffix}",
                cabin="economy",
                currency="CNY",
                total_price_minor=12_000,
                offer_metadata={},
            )
            session.add(offer)
            await session.flush()
            session.add(
                PriceObservation(
                    id=uuid4(),
                    search_query_id=query.id,
                    provider_id=provider.id,
                    collection_run_id=run.id,
                    itinerary_id=itinerary.id,
                    fare_offer_id=offer.id,
                    offer_fingerprint=offer.fingerprint,
                    observed_at=observed_at,
                    currency="CNY",
                    total_price_minor=12_000,
                    is_lowest=True,
                    is_direct=True,
                )
            )
            ignored_itinerary = Itinerary(
                collection_run_id=run.id,
                search_query_id=query.id,
                provider_id=provider.id,
                provider_itinerary_id="ignored-itinerary",
                fingerprint=f"ignored-itinerary-{suffix}",
                total_duration_minutes=180,
                stop_count=0,
                is_direct=True,
                leg_count=1,
                itinerary_metadata={},
            )
            session.add(ignored_itinerary)
            await session.flush()
            session.add(
                Segment(
                    itinerary_id=ignored_itinerary.id,
                    position=0,
                    leg_position=0,
                    marketing_airline_code="YY",
                    flight_number="YY001",
                    origin_airport_code="PVG",
                    destination_airport_code="NRT",
                    departure_at_utc=observed_at,
                    arrival_at_utc=observed_at,
                    departure_local=observed_at.replace(tzinfo=None),
                    arrival_local=observed_at.replace(tzinfo=None),
                    departure_timezone="Asia/Shanghai",
                    arrival_timezone="Asia/Tokyo",
                    duration_minutes=180,
                    segment_metadata={},
                )
            )
            ignored_offer = FareOffer(
                collection_run_id=run.id,
                itinerary_id=ignored_itinerary.id,
                provider_offer_id="ignored-offer",
                fingerprint=f"ignored-offer-{suffix}",
                cabin="economy",
                currency="CNY",
                total_price_minor=1_000,
                offer_metadata={},
            )
            session.add(ignored_offer)
            await session.flush()
            session.add(
                PriceObservation(
                    id=uuid4(),
                    search_query_id=query.id,
                    provider_id=provider.id,
                    collection_run_id=run.id,
                    itinerary_id=ignored_itinerary.id,
                    fare_offer_id=ignored_offer.id,
                    offer_fingerprint=ignored_offer.fingerprint,
                    observed_at=observed_at,
                    currency="CNY",
                    total_price_minor=1_000,
                    is_lowest=True,
                    is_direct=True,
                )
            )
            await session.flush()

            first = await evaluate_collection_run(session, run_id=run.id, now=observed_at)
            second = await evaluate_collection_run(session, run_id=run.id, now=observed_at)

            assert first.created_events == 1
            assert first.created_deliveries == 1
            assert second.created_events == 0
            assert await session.scalar(
                select(AlertEvent.id).where(AlertEvent.alert_rule_id == rule.id)
            ) is not None
            assert await session.scalar(
                select(NotificationDelivery.id).join(AlertEvent)
                .where(AlertEvent.alert_rule_id == rule.id)
            ) is not None
            event = await session.scalar(
                select(AlertEvent).where(AlertEvent.alert_rule_id == rule.id)
            )
            assert event is not None
            assert event.event_payload["priceMinor"] == 12_000
            works = await claim_pending_deliveries(
                session,
                secret_box=secret_box,
                now=observed_at,
            )
            assert len(works) == 1
            assert works[0].destination == "https://hooks.example.test/fare"
            delivery = await session.get(NotificationDelivery, works[0].delivery_id)
            assert delivery is not None
            delivery.status = "pending"
            delivery.attempt_count = 0
            delivery.next_attempt_at = observed_at
            encrypted_destination = channel.secret_ciphertext
            channel.secret_ciphertext = None
            channel.timezone = "Asia/Shanghai"
            channel.quiet_hours_start = time(22)
            channel.quiet_hours_end = time(8)
            channel.allowed_weekdays = [2]
            await session.flush()

            deferred = await claim_pending_deliveries(
                session,
                secret_box=secret_box,
                now=observed_at,
            )
            assert deferred == []
            assert delivery.status == "pending"
            assert delivery.attempt_count == 0
            assert delivery.error_code is None
            assert delivery.next_attempt_at == datetime(2026, 7, 22, 0, tzinfo=UTC)

            channel.secret_ciphertext = encrypted_destination
            await session.flush()
            resumed_at = datetime(2026, 7, 22, 0, tzinfo=UTC)
            works = await claim_pending_deliveries(
                session,
                secret_box=secret_box,
                now=resumed_at,
            )
            assert len(works) == 1
            assert works[0].attempt_count == 1
            await finish_delivery(
                session,
                delivery_id=works[0].delivery_id,
                result=DeliveryResult(
                    success=True,
                    retryable=False,
                    response_metadata={"httpStatus": 204},
                ),
                now=resumed_at,
            )
            assert delivery.status == "succeeded"
            assert delivery.sent_at == resumed_at
            assert run.alerts_evaluated_at == observed_at
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()

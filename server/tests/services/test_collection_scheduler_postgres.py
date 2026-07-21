from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.collectors.runtime import (
    BrowserRunConfig,
    BrowserRunResult,
    CaptureDiagnostic,
    CapturedPayload,
    CaptureRule,
    FailureKind,
)
from app.models import (
    CalendarPriceObservation,
    CollectionRun,
    FareOffer,
    PriceObservation,
    Provider,
    SearchLeg,
    SearchQuery,
    Subscription,
    User,
)
from app.models.enums import CollectionStatus
from app.services.collection_dispatch import (
    DispatchLease,
    dispatch_collection_run_safely,
    lease_collection_runs,
    publish_collection_run,
    release_dispatch_leases,
)
from app.services.collection_runs import ensure_on_demand_collection_run
from app.services.collection_scheduler import plan_scheduler_tick
from app.settings import Settings
from app.tasks.collection import (
    CollectionRunUnavailableError,
    _claim_collection_run,
    run_collection_once,
)
from app.tasks.scheduler import run_scheduler_tick

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "ctrip"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_scheduler_groups_subscribers_and_enforces_lease_ownership() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            provider = await session.scalar(select(Provider).where(Provider.code == "ctrip"))
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
                normalized_query={"scheduler_fixture": True},
            )
            user_token = uuid4().hex
            user = User(
                username=f"scheduler-{user_token}",
                normalized_username=f"scheduler-{user_token}",
                email=f"scheduler-{user_token}@example.test",
                display_name=f"scheduler-{user_token}",
                role="member",
                status="active",
            )
            session.add_all([query, user])
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
            subscriptions = [
                Subscription(
                    user_id=user.id,
                    search_query_id=query.id,
                    name=f"Shared route {position}",
                    enabled=True,
                    poll_interval_seconds=900,
                    next_due_at=now,
                    tags=[],
                )
                for position in range(2)
            ]
            session.add_all(subscriptions)
            await session.flush()

            plan = await plan_scheduler_tick(
                session,
                now=now,
                subscription_batch_size=100,
                dispatch_batch_size=10,
                dispatch_lease_seconds=120,
                schedule_bucket_seconds=300,
            )

            assert plan.due_subscription_count == 2
            assert plan.grouped_query_count == 1
            assert plan.created_run_count == 1
            assert len(plan.dispatch_leases) == 1
            assert {item.next_due_at for item in subscriptions} == {
                now + timedelta(seconds=900)
            }

            first_lease = plan.dispatch_leases[0]
            run = await session.get(CollectionRun, first_lease.run_id)
            assert run is not None
            assert run.status == CollectionStatus.LEASED.value

            failed_publish = publish_collection_run(
                first_lease,
                publisher=lambda _lease: (_ for _ in ()).throw(ConnectionError()),
            )
            assert failed_publish.enqueued is False
            assert await release_dispatch_leases(
                session,
                (first_lease,),
                now=now,
                retry_delay_seconds=30,
            ) == 1
            assert run.status == CollectionStatus.PENDING.value
            assert run.scheduled_at == now + timedelta(seconds=30)

            second_leases = await lease_collection_runs(
                session,
                now=now + timedelta(seconds=30),
                limit=1,
                lease_seconds=120,
                run_id=run.id,
            )
            assert len(second_leases) == 1
            second_lease = second_leases[0]

            with pytest.raises(CollectionRunUnavailableError, match="token"):
                await _claim_collection_run(
                    session,
                    run_id=run.id,
                    worker_id="collector-test",
                    dispatch_token="wrong-token",
                    lease_seconds=900,
                )

            claim = await _claim_collection_run(
                session,
                run_id=run.id,
                worker_id="collector-test",
                dispatch_token=second_lease.token,
                lease_seconds=900,
            )
            assert claim.run_id == run.id
            assert run.status == CollectionStatus.RUNNING.value
            assert run.attempt == 1
            assert run.lease_owner == "worker:collector-test"

            run.attempt = run.max_attempts
            run.lease_expires_at = now - timedelta(seconds=1)
            recovery = await plan_scheduler_tick(
                session,
                now=now + timedelta(seconds=60),
                subscription_batch_size=100,
                dispatch_batch_size=10,
                dispatch_lease_seconds=120,
                schedule_bucket_seconds=300,
            )
            assert recovery.recovered_run_count == 1
            assert recovery.exhausted_run_count == 1
            assert run.status == CollectionStatus.FAILED.value
            assert run.error_code == "lease_expired_attempts_exhausted"
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def test_scheduler_task_commits_before_publishing_grouped_run() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
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
                query, user = await _create_query_user_and_leg(session, suffix="task")
                session.add_all(
                    Subscription(
                        user_id=user.id,
                        search_query_id=query.id,
                        name=f"Task route {position}",
                        enabled=True,
                        poll_interval_seconds=900,
                        next_due_at=now,
                        tags=[],
                    )
                    for position in range(2)
                )

            published: list[DispatchLease] = []

            def publish(lease: DispatchLease) -> object:
                published.append(lease)
                return object()

            result = await run_scheduler_tick(
                settings=Settings(
                    collection_scheduler_subscription_batch_size=100,
                    collection_scheduler_dispatch_batch_size=10,
                    collection_dispatch_lease_seconds=120,
                    collection_schedule_bucket_seconds=300,
                ),
                session_factory=factory,
                publisher=publish,
                now=now,
            )

            assert result["status"] == "ok"
            assert result["due_subscription_count"] == 2
            assert result["grouped_query_count"] == 1
            assert result["created_run_count"] == 1
            assert result["enqueued_run_count"] == 1
            assert len(published) == 1
            async with factory() as session:
                run = await session.get(CollectionRun, published[0].run_id)
                assert run is not None
                assert run.status == CollectionStatus.LEASED.value
                assert run.lease_owner == published[0].lease_owner
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_scheduler_to_collector_persists_a_grouped_run_end_to_end() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                query, user = await _create_query_user_and_leg(session, suffix="end-to-end")
                subscription = Subscription(
                    user_id=user.id,
                    search_query_id=query.id,
                    name="End-to-end route",
                    enabled=True,
                    poll_interval_seconds=900,
                    next_due_at=now,
                    tags=[],
                )
                session.add(subscription)

            published: list[DispatchLease] = []

            def publish(lease: DispatchLease) -> object:
                published.append(lease)
                return object()

            scheduler_result = await run_scheduler_tick(
                settings=Settings(
                    collection_scheduler_subscription_batch_size=100,
                    collection_scheduler_dispatch_batch_size=10,
                    collection_dispatch_lease_seconds=120,
                    collection_schedule_bucket_seconds=300,
                    collection_provider_concurrency=1,
                    collection_route_concurrency=1,
                    collection_minimum_interval_seconds=0,
                    collection_jitter_seconds=0,
                    collection_capture_settle_seconds=0,
                ),
                session_factory=factory,
                publisher=publish,
                now=now,
            )

            assert scheduler_result["created_run_count"] == 1
            assert scheduler_result["enqueued_run_count"] == 1
            assert len(published) == 1

            gate = _RecordingGate()
            result = await run_collection_once(
                published[0].run_id,
                dispatch_token=published[0].token,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                    collection_provider_concurrency=1,
                    collection_route_concurrency=1,
                    collection_minimum_interval_seconds=0,
                    collection_jitter_seconds=0,
                    collection_capture_settle_seconds=0,
                ),
                session_factory=factory,
                runner=_FixtureRunner(),  # type: ignore[arg-type]
                rate_gate=gate,  # type: ignore[arg-type]
                worker_id="end-to-end-worker",
            )

            assert result["status"] == CollectionStatus.SUCCEEDED.value
            assert result["calendar_count"] > 0
            assert result["offer_count"] > 0
            assert gate.routes == [("ctrip", f"{query.query_hash[:16]}:SHA-TYO")]

            async with factory() as session:
                run = await session.get(CollectionRun, published[0].run_id)
                assert run is not None
                assert run.status == CollectionStatus.SUCCEEDED.value
                assert run.lease_owner is None
                assert await _count(session, CalendarPriceObservation, run.id) > 0
                assert await _count(session, FareOffer, run.id) > 0
                assert await _count(session, PriceObservation, run.id) > 0
                saved_subscription = await session.get(Subscription, subscription.id)
                assert saved_subscription is not None
                assert saved_subscription.last_collected_at is not None
                assert saved_subscription.next_due_at > saved_subscription.last_collected_at
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_on_demand_dispatch_to_collector_persists_a_run_end_to_end() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
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
                query, _ = await _create_query_user_and_leg(session, suffix="on-demand-e2e")
                run = await ensure_on_demand_collection_run(
                    session,
                    search_query=query,
                    now=now,
                )
                run_id = run.id

            published: list[DispatchLease] = []

            def publish(lease: DispatchLease) -> object:
                published.append(lease)
                return object()

            dispatch_result = await dispatch_collection_run_safely(
                factory,
                run_id=run_id,
                lease_seconds=120,
                publisher=publish,
                now=now,
            )
            assert dispatch_result.enqueued is True
            assert len(published) == 1

            result = await run_collection_once(
                run_id,
                dispatch_token=published[0].token,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                    collection_provider_concurrency=1,
                    collection_route_concurrency=1,
                    collection_minimum_interval_seconds=0,
                    collection_jitter_seconds=0,
                    collection_capture_settle_seconds=0,
                ),
                session_factory=factory,
                runner=_FixtureRunner(),  # type: ignore[arg-type]
                rate_gate=_RecordingGate(),  # type: ignore[arg-type]
                worker_id="on-demand-e2e-worker",
            )

            assert result["status"] == CollectionStatus.SUCCEEDED.value
            assert result["offer_count"] > 0
            async with factory() as session:
                persisted = await session.get(CollectionRun, run_id)
                assert persisted is not None
                assert persisted.status == CollectionStatus.SUCCEEDED.value
                assert persisted.run_metadata["trigger"] == "on_demand"
                assert await _count(session, FareOffer, run_id) > 0
                assert await _count(session, PriceObservation, run_id) > 0
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_on_demand_dispatch_returns_pending_when_broker_is_unavailable() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
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
                query, _ = await _create_query_user_and_leg(session, suffix="on-demand")
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"on-demand-test:{uuid4()}",
                    status=CollectionStatus.PENDING.value,
                    scheduled_at=now,
                    run_metadata={"trigger": "on_demand_test"},
                )
                session.add(run)
                await session.flush()
                run_id = run.id

            def unavailable_broker(_lease: DispatchLease) -> object:
                raise ConnectionError("broker unavailable")

            result = await dispatch_collection_run_safely(
                factory,
                run_id=run_id,
                lease_seconds=120,
                publisher=unavailable_broker,
                now=now,
            )

            assert result.enqueued is False
            assert result.error_type == "ConnectionError"
            async with factory() as session:
                persisted = await session.get(CollectionRun, run_id)
                assert persisted is not None
                assert persisted.status == CollectionStatus.PENDING.value
                assert persisted.lease_owner is None
                assert persisted.lease_expires_at is None
                assert persisted.scheduled_at > now
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_anti_bot_failure_keeps_classification_and_respects_retry_delay() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
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
                query, _ = await _create_query_user_and_leg(session, suffix="anti-bot")
                run = CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"anti-bot-test:{uuid4()}",
                    status=CollectionStatus.PENDING.value,
                    attempt=0,
                    max_attempts=3,
                    scheduled_at=now,
                    run_metadata={"trigger": "anti_bot_test"},
                )
                session.add(run)
                await session.flush()
                run_id = run.id

            runner = _AntiBotRunner()
            result = await run_collection_once(
                run_id,
                settings=Settings(
                    collector_browser_channel="chrome",
                    collection_run_lease_seconds=900,
                    collection_retry_base_seconds=60,
                    collection_retry_max_seconds=600,
                ),
                session_factory=factory,
                runner=runner,  # type: ignore[arg-type]
                worker_id="anti-bot-worker",
            )

            assert result["status"] == CollectionStatus.PENDING.value
            assert result["error_code"] == FailureKind.ANTI_BOT_432.value
            assert result["retryable"] is True
            assert result["retry_scheduled_at"] is not None
            assert runner.configs[0].browser_channel == "chrome"
            async with factory() as session:
                persisted = await session.get(CollectionRun, run_id)
                assert persisted is not None
                assert persisted.status == CollectionStatus.PENDING.value
                assert persisted.attempt == 1
                assert persisted.error_code == FailureKind.ANTI_BOT_432.value
                assert persisted.run_metadata["failure"]["retryable"] is True
                assert persisted.scheduled_at > datetime.now(UTC)

            duplicate = await run_collection_once(
                run_id,
                settings=Settings(),
                session_factory=factory,
                runner=_AntiBotRunner(),  # type: ignore[arg-type]
                worker_id="duplicate-worker",
            )
            assert duplicate["status"] == "skipped"
            assert "future retry" in duplicate["reason"]
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _create_query_user_and_leg(
    session: AsyncSession,
    *,
    suffix: str,
) -> tuple[SearchQuery, User]:
    token = uuid4().hex
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
        normalized_query={"scheduler_fixture": suffix},
    )
    user = User(
        username=f"scheduler-{suffix}-{token}",
        normalized_username=f"scheduler-{suffix}-{token}",
        email=f"scheduler-{suffix}-{token}@example.test",
        display_name=f"scheduler-{suffix}-{token}",
        role="member",
        status="active",
    )
    session.add_all([query, user])
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
    await session.flush()
    return query, user


async def _count(session: AsyncSession, model: type[object], run_id) -> int:
    value = await session.scalar(
        select(func.count()).select_from(model).where(model.collection_run_id == run_id)
    )
    return int(value or 0)


class _RecordingGate:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str]] = []

    @asynccontextmanager
    async def slot(self, provider: str, route_key: str):
        self.routes.append((provider, route_key))
        yield


class _FixtureRunner:
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
                url_without_query="https://m.ctrip.com/restapi/FlightIntlAndInlandLowestPriceSearch",
                received_at=finished_at,
                payload=json.loads(
                    (FIXTURE_ROOT / "calendar_one_way.json").read_text(encoding="utf-8")
                ),
            ),
            CapturedPayload(
                provider="ctrip",
                route_key=config.route_key,
                capture_name="batch_search",
                status_code=200,
                url_without_query="https://flights.ctrip.com/international/search/api/search/batchSearch",
                received_at=finished_at,
                payload=deepcopy(
                    json.loads(
                        (FIXTURE_ROOT / "batch_search.json").read_text(encoding="utf-8")
                    )
                ),
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


class _AntiBotRunner:
    def __init__(self) -> None:
        self.configs: list[BrowserRunConfig] = []

    async def run(
        self,
        config: BrowserRunConfig,
        *,
        capture_rules: tuple[CaptureRule, ...],
    ) -> BrowserRunResult:
        assert capture_rules
        self.configs.append(config)
        finished_at = datetime.now(UTC)
        return BrowserRunResult(
            provider=config.provider,
            route_key=config.route_key,
            started_at=finished_at - timedelta(seconds=1),
            finished_at=finished_at,
            captures=(),
            diagnostics=(
                CaptureDiagnostic(
                    kind=FailureKind.ANTI_BOT_432,
                    message="Provider returned HTTP 432 for the page navigation",
                    provider=config.provider,
                    route_key=config.route_key,
                    status_code=432,
                    retryable=True,
                ),
            ),
            expected_capture_names=config.expected_capture_names,
        )

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.runtime import BrowserRunResult, CaptureDiagnostic, FailureKind
from app.models import CollectionRun, Provider, SearchLeg, SearchQuery
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


async def test_task_publishes_running_retry_and_terminal_only_after_commit() -> None:
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
                    normalized_query={"realtime_publish_test": True},
                )
                session.add(query)
                await session.flush()
                session.add(
                    SearchLeg(
                        search_query_id=query.id,
                        position=0,
                        origin_code="SHA",
                        destination_code="TYO",
                        departure_date=date(2027, 8, 15),
                    )
                )
                retry_run = _run(query.id, provider.id, now, max_attempts=3)
                terminal_run = _run(query.id, provider.id, now, max_attempts=1)
                session.add_all([retry_run, terminal_run])

            observed: dict[UUID, list[str]] = {retry_run.id: [], terminal_run.id: []}

            async def publisher(run_id: UUID) -> None:
                async with factory() as session:
                    persisted = await session.get(CollectionRun, run_id)
                    assert persisted is not None
                    observed[run_id].append(persisted.status)
                    if persisted.status == "failed":
                        raise RuntimeError("simulated realtime outage after commit")

            settings = Settings(
                _env_file=None,
                collection_run_lease_seconds=900,
                collection_retry_base_seconds=5,
                collection_retry_max_seconds=30,
                collection_retry_jitter_ratio=0,
                collection_capture_settle_seconds=0,
                collection_minimum_interval_seconds=0,
                collection_jitter_seconds=0,
            )
            retry_result = await run_collection_once(
                retry_run.id,
                settings=settings,
                session_factory=factory,
                runner=_FailingRunner(),  # type: ignore[arg-type]
                rate_gate=_NoopGate(),  # type: ignore[arg-type]
                state_publisher=publisher,
                worker_id="realtime-test",
            )
            terminal_result = await run_collection_once(
                terminal_run.id,
                settings=settings,
                session_factory=factory,
                runner=_FailingRunner(),  # type: ignore[arg-type]
                rate_gate=_NoopGate(),  # type: ignore[arg-type]
                state_publisher=publisher,
                worker_id="realtime-test",
            )

            assert retry_result["status"] == "pending"
            assert terminal_result["status"] == "failed"
            assert observed[retry_run.id] == ["running", "pending"]
            assert observed[terminal_run.id] == ["running", "failed"]
            async with factory() as session:
                assert (await session.get(CollectionRun, terminal_run.id)).status == "failed"
        finally:
            await transaction.rollback()
    await engine.dispose()


def _run(query_id: UUID, provider_id: UUID, now: datetime, *, max_attempts: int) -> CollectionRun:
    return CollectionRun(
        search_query_id=query_id,
        provider_id=provider_id,
        idempotency_key=f"realtime-publish:{uuid4().hex}",
        status="pending",
        attempt=0,
        max_attempts=max_attempts,
        scheduled_at=now,
        run_metadata={},
    )


class _FailingRunner:
    async def run(self, config, *, capture_rules) -> BrowserRunResult:
        now = datetime.now(UTC)
        return BrowserRunResult(
            provider=config.provider,
            route_key=config.route_key,
            started_at=now,
            finished_at=now,
            captures=(),
            diagnostics=(
                CaptureDiagnostic(
                    kind=FailureKind.TIMEOUT,
                    message="simulated timeout",
                    provider=config.provider,
                    route_key=config.route_key,
                    retryable=True,
                ),
            ),
            expected_capture_names=config.expected_capture_names,
        )


class _NoopGate:
    @asynccontextmanager
    async def slot(self, _provider: str, _route: str):
        yield

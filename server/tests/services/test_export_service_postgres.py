from __future__ import annotations

import asyncio
import csv
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.partitions import price_observation_partition_ddl
from app.models import (
    CollectionRun,
    ExportJob,
    ExportJobCollectionRun,
    FareOffer,
    Itinerary,
    PriceObservation,
    Provider,
    SearchLeg,
    SearchQuery,
    Subscription,
    User,
)
from app.models.enums import ExportStatus
from app.services.export_data import load_export_observation_page
from app.services.export_files import GeneratedExport
from app.services.export_jobs import (
    ExportJobBusyError,
    ExportJobNotFoundError,
    ExportSourceUnavailableError,
    create_export_job,
    fail_stale_pending_export_jobs,
    get_owned_export_job,
    lease_dispatchable_export_ids,
    mark_export_dispatch_published,
)
from app.settings import Settings
from app.tasks.exports import maintain_exports_once, run_export_job_once

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_export_manifest_freezes_creation_visible_collection_runs() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_visible = datetime(2098, 4, 5, 8, tzinfo=UTC)
    observed_inflight = datetime(2098, 4, 5, 9, tzinfo=UTC)
    observed_later = datetime(2098, 4, 5, 10, tzinfo=UTC)
    suffix = uuid4().hex
    user_id = None
    query_id = None
    provider_id = None
    writer: AsyncSession | None = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text(price_observation_partition_ddl(observed_visible)))

        async with factory() as session, session.begin():
            user = User(
                username=f"manifest-{suffix}",
                normalized_username=f"manifest-{suffix}",
                display_name="Manifest owner",
                role="member",
                status="active",
            )
            provider = Provider(
                code=f"manifest-{suffix[:20]}",
                display_name="Manifest provider",
                enabled=True,
            )
            query = SearchQuery(
                provider=provider.code,
                query_hash=uuid4().hex + uuid4().hex,
                trip_type="one_way",
                adults=1,
                children=0,
                infants=0,
                cabin="economy",
                currency="CNY",
                direct_only=False,
                normalized_query={"fixture": "manifest-visibility"},
            )
            session.add_all((user, provider, query))
            await session.flush()
            subscription = Subscription(
                user_id=user.id,
                search_query_id=query.id,
                name="Manifest visibility",
                enabled=True,
                poll_interval_seconds=21_600,
                tags=[],
            )
            session.add_all(
                (
                    subscription,
                    SearchLeg(
                        search_query_id=query.id,
                        position=0,
                        origin_code="SHA",
                        destination_code="TYO",
                        departure_date=date(2098, 6, 1),
                    ),
                )
            )
            visible_run, visible_observation = await _add_succeeded_observation_run(
                session,
                query=query,
                provider=provider,
                observed_at=observed_visible,
                suffix=f"visible-{suffix}",
                price_minor=120_000,
            )
            range_start = datetime(2098, 4, 1, tzinfo=UTC)
            range_end = datetime(2098, 5, 1, tzinfo=UTC)
            boundary_runs = tuple(
                CollectionRun(
                    search_query_id=query.id,
                    provider_id=provider.id,
                    idempotency_key=f"manifest-boundary-{position}:{suffix}",
                    status="succeeded",
                    attempt=1,
                    max_attempts=3,
                    scheduled_at=finished_at,
                    started_at=finished_at,
                    finished_at=finished_at,
                    run_metadata={"fixture": "manifest-boundary"},
                )
                for position, finished_at in enumerate(
                    (range_start, range_start - timedelta(seconds=1), range_end)
                )
            )
            session.add_all(boundary_runs)
            await session.flush()
            user_id = user.id
            query_id = query.id
            provider_id = provider.id

        writer = factory()
        await writer.begin()
        inflight_run, _ = await _add_succeeded_observation_run(
            writer,
            query=query,
            provider=provider,
            observed_at=observed_inflight,
            suffix=f"inflight-{suffix}",
            price_minor=119_000,
        )
        await writer.flush()

        before_manifest = datetime.now(UTC)
        async with factory() as session, session.begin():
            job, created = await create_export_job(
                session,
                user_id=user.id,
                subscription_id=subscription.id,
                idempotency_key=f"manifest-job-{suffix}",
                export_format="json",
                range_start=range_start,
                range_end=range_end,
                max_attempts=3,
                max_active_jobs=5,
                max_global_active_jobs=100,
                max_manifest_runs=20_000,
                max_file_bytes=1_048_576,
                max_retained_files=20,
                max_retained_bytes=20 * 1_048_576,
                dispatch_lease_seconds=300,
            )
            assert created is True
            assert job.snapshot_at >= before_manifest
            job_id = job.id
            job_created_at = job.created_at
        assert job.snapshot_at <= datetime.now(UTC)

        with pytest.raises(ExportJobBusyError, match="source run limit"):
            async with factory() as session, session.begin():
                await create_export_job(
                    session,
                    user_id=user.id,
                    subscription_id=subscription.id,
                    idempotency_key=f"manifest-too-large-{suffix}",
                    export_format="json",
                    range_start=range_start,
                    range_end=range_end,
                    max_attempts=3,
                    max_active_jobs=5,
                    max_global_active_jobs=100,
                    max_manifest_runs=1,
                    max_file_bytes=1_048_576,
                    max_retained_files=20,
                    max_retained_bytes=20 * 1_048_576,
                    dispatch_lease_seconds=300,
                )

        async with factory() as session, session.begin():
            first_page = await load_export_observation_page(
                session,
                job_id=job_id,
                search_query_id=query.id,
                range_start=datetime(2098, 4, 1, tzinfo=UTC),
                range_end=datetime(2098, 5, 1, tzinfo=UTC),
                after_observed_at=None,
                after_id=None,
                limit=1,
            )
        assert [row.id for row in first_page] == [visible_observation.id]

        await writer.commit()
        await writer.close()
        writer = None

        async with factory() as session, session.begin():
            later_run, _ = await _add_succeeded_observation_run(
                session,
                query=query,
                provider=provider,
                observed_at=observed_later,
                suffix=f"later-{suffix}",
                price_minor=118_000,
            )

        async with factory() as session, session.begin():
            replay, replay_created = await create_export_job(
                session,
                user_id=user.id,
                subscription_id=subscription.id,
                idempotency_key=f"manifest-job-{suffix}",
                export_format="json",
                range_start=range_start,
                range_end=range_end,
                max_attempts=3,
                max_active_jobs=5,
                max_global_active_jobs=100,
                max_manifest_runs=20_000,
                max_file_bytes=1_048_576,
                max_retained_files=20,
                max_retained_bytes=20 * 1_048_576,
                dispatch_lease_seconds=300,
            )
            assert replay.id == job_id
            assert replay_created is False

        async with factory() as session, session.begin():
            later_page = await load_export_observation_page(
                session,
                job_id=job_id,
                search_query_id=query.id,
                range_start=datetime(2098, 4, 1, tzinfo=UTC),
                range_end=datetime(2098, 5, 1, tzinfo=UTC),
                after_observed_at=first_page[-1].observed_at,
                after_id=first_page[-1].id,
                limit=100,
            )
            full_page = await load_export_observation_page(
                session,
                job_id=job_id,
                search_query_id=query.id,
                range_start=datetime(2098, 4, 1, tzinfo=UTC),
                range_end=datetime(2098, 5, 1, tzinfo=UTC),
                after_observed_at=None,
                after_id=None,
                limit=100,
            )
            manifest_run_ids = set(
                (
                    await session.scalars(
                        select(ExportJobCollectionRun.collection_run_id).where(
                            ExportJobCollectionRun.export_job_id == job_id
                        )
                    )
                ).all()
            )

        assert later_page == ()
        assert [row.id for row in full_page] == [visible_observation.id]
        assert manifest_run_ids == {visible_run.id, boundary_runs[0].id}
        assert inflight_run.id not in manifest_run_ids
        assert later_run.id not in manifest_run_ids
        assert boundary_runs[1].id not in manifest_run_ids
        assert boundary_runs[2].id not in manifest_run_ids

        async with factory() as session, session.begin():
            assert (
                await fail_stale_pending_export_jobs(
                    session,
                    timeout_seconds=86_400,
                    limit=20,
                    now=job_created_at + timedelta(seconds=86_401),
                )
                == 1
            )
        async with factory() as session:
            timed_out = await session.get(ExportJob, job_id)
            assert timed_out is not None
            assert timed_out.status == ExportStatus.FAILED.value
            assert timed_out.error_code == "queue_timeout"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ExportJobCollectionRun)
                    .where(ExportJobCollectionRun.export_job_id == job_id)
                )
            ) == 0
    finally:
        if writer is not None:
            await writer.rollback()
            await writer.close()
        if user_id is not None and query_id is not None:
            async with factory() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                run_ids = select(CollectionRun.id).where(CollectionRun.search_query_id == query_id)
                await session.execute(
                    delete(PriceObservation).where(PriceObservation.search_query_id == query_id)
                )
                await session.execute(
                    delete(FareOffer).where(FareOffer.collection_run_id.in_(run_ids))
                )
                await session.execute(
                    delete(Itinerary).where(Itinerary.collection_run_id.in_(run_ids))
                )
                await session.execute(
                    delete(CollectionRun).where(CollectionRun.search_query_id == query_id)
                )
                await session.execute(
                    delete(SearchLeg).where(SearchLeg.search_query_id == query_id)
                )
                await session.execute(delete(SearchQuery).where(SearchQuery.id == query_id))
                if provider_id is not None:
                    await session.execute(delete(Provider).where(Provider.id == provider_id))
        await engine.dispose()


async def test_export_creation_requires_complete_hot_or_archived_month_sources() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        suffix = uuid4().hex
        hot_month = date(1987, 3, 1)
        archive_month = date(1987, 2, 1)
        purged_month = date(1987, 1, 1)
        future_month = date(datetime.now(UTC).year + 20, 1, 1)
        try:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS farescope_archive"))
            for month in (hot_month, archive_month, purged_month):
                await connection.execute(text(price_observation_partition_ddl(month)))
            for month in (archive_month, purged_month):
                partition_name = f"price_observations_y{month.year:04d}m{month.month:02d}"
                await connection.execute(
                    text(
                        "ALTER TABLE public.price_observations "
                        f"DETACH PARTITION public.{partition_name}"
                    )
                )
                await connection.execute(
                    text(f"ALTER TABLE public.{partition_name} SET SCHEMA farescope_archive")
                )
            await connection.execute(
                text("DROP TABLE farescope_archive.price_observations_y1987m01")
            )

            async with factory() as session, session.begin():
                user = User(
                    username=f"source-coverage-{suffix}",
                    normalized_username=f"source-coverage-{suffix}",
                    display_name="Source coverage",
                    role="member",
                    status="active",
                )
                provider = Provider(
                    code=f"source-coverage-{suffix[:20]}",
                    display_name="Source coverage provider",
                    enabled=True,
                )
                query = SearchQuery(
                    provider=provider.code,
                    query_hash=uuid4().hex + uuid4().hex,
                    trip_type="one_way",
                    adults=1,
                    children=0,
                    infants=0,
                    cabin="economy",
                    currency="CNY",
                    direct_only=False,
                    normalized_query={"fixture": "export-source-coverage"},
                )
                session.add_all((user, provider, query))
                await session.flush()
                subscription = Subscription(
                    user_id=user.id,
                    search_query_id=query.id,
                    name="Source coverage",
                    enabled=True,
                    poll_interval_seconds=21_600,
                    tags=[],
                )
                session.add_all(
                    (
                        subscription,
                        SearchLeg(
                            search_query_id=query.id,
                            position=0,
                            origin_code="SHA",
                            destination_code="TYO",
                            departure_date=date(2027, 1, 1),
                        ),
                    )
                )
                session.add_all(
                    CollectionRun(
                        search_query_id=query.id,
                        provider_id=provider.id,
                        idempotency_key=f"source-coverage-{month.isoformat()}-{suffix}",
                        status="succeeded",
                        attempt=1,
                        max_attempts=3,
                        scheduled_at=datetime(month.year, month.month, 15, tzinfo=UTC),
                        started_at=datetime(month.year, month.month, 15, tzinfo=UTC),
                        finished_at=datetime(month.year, month.month, 15, tzinfo=UTC),
                        run_metadata={"fixture": "export-source-coverage"},
                    )
                    for month in (hot_month, archive_month, purged_month, future_month)
                )
                await session.flush()
                user_id = user.id
                subscription_id = subscription.id

            async def create_for_range(range_start: date, range_end: date, key: str):
                async with factory() as session, session.begin():
                    return await create_export_job(
                        session,
                        user_id=user_id,
                        subscription_id=subscription_id,
                        idempotency_key=key,
                        export_format="json",
                        range_start=datetime(
                            range_start.year,
                            range_start.month,
                            range_start.day,
                            tzinfo=UTC,
                        ),
                        range_end=datetime(
                            range_end.year,
                            range_end.month,
                            range_end.day,
                            tzinfo=UTC,
                        ),
                        max_attempts=3,
                        max_active_jobs=5,
                        max_global_active_jobs=100,
                        max_manifest_runs=20_000,
                        max_file_bytes=1_048_576,
                        max_retained_files=20,
                        max_retained_bytes=20 * 1_048_576,
                        dispatch_lease_seconds=300,
                    )

            async def create_for_month(month: date, key: str):
                end_month = (
                    date(month.year + 1, 1, 1)
                    if month.month == 12
                    else date(month.year, month.month + 1, 1)
                )
                return await create_for_range(month, end_month, key)

            hot_job, hot_created = await create_for_month(hot_month, f"hot-source-{suffix}")
            archive_job, archive_created = await create_for_month(
                archive_month,
                f"archive-source-{suffix}",
            )
            assert hot_created is True
            assert archive_created is True
            assert hot_job.id != archive_job.id

            empty_job, empty_created = await create_for_range(
                date(1977, 1, 1),
                date(1978, 1, 1),
                f"empty-wide-source-{suffix}",
            )
            assert empty_created is True
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ExportJobCollectionRun)
                        .where(ExportJobCollectionRun.export_job_id == empty_job.id)
                    )
                ) == 0

            missing_key = f"purged-source-{suffix}"
            async with factory() as session:
                manifest_count_before = await session.scalar(
                    select(func.count())
                    .select_from(ExportJobCollectionRun)
                    .join(ExportJob, ExportJob.id == ExportJobCollectionRun.export_job_id)
                    .where(ExportJob.user_id == user_id)
                )
            with pytest.raises(
                ExportSourceUnavailableError,
                match=r"UTC month\(s\): 1987-01",
            ):
                await create_for_month(purged_month, missing_key)
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ExportJob)
                        .where(ExportJob.idempotency_key == missing_key)
                    )
                ) == 0
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ExportJobCollectionRun)
                        .join(ExportJob, ExportJob.id == ExportJobCollectionRun.export_job_id)
                        .where(ExportJob.user_id == user_id)
                    )
                ) == manifest_count_before

            default_exists = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_inherits
                        JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                        JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                        JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                        WHERE parent_ns.nspname = 'public'
                          AND parent.relname = 'price_observations'
                          AND pg_get_expr(child.relpartbound, child.oid, true) = 'DEFAULT'
                    )
                    """
                )
            )
            if not default_exists:
                await connection.execute(
                    text(
                        "CREATE TABLE price_observations_default_export_test "
                        "PARTITION OF price_observations DEFAULT"
                    )
                )
            default_job, default_created = await create_for_month(
                future_month,
                f"default-source-{suffix}",
            )
            assert default_created is True
            assert default_job.id not in {hot_job.id, archive_job.id}
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_running_export_reservation_is_atomic_across_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    user_ids = []
    query_id = None
    try:
        async with factory() as session, session.begin():
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
                normalized_query={"fixture": "global-export-reservation"},
            )
            users = (
                User(
                    username=f"reserve-a-{suffix}",
                    normalized_username=f"reserve-a-{suffix}",
                    display_name="Reserve A",
                    role="member",
                    status="active",
                ),
                User(
                    username=f"reserve-b-{suffix}",
                    normalized_username=f"reserve-b-{suffix}",
                    display_name="Reserve B",
                    role="member",
                    status="active",
                ),
            )
            session.add_all((query, *users))
            await session.flush()
            subscriptions = tuple(
                Subscription(
                    user_id=user.id,
                    search_query_id=query.id,
                    name=f"Atomic reservation {position}",
                    enabled=True,
                    poll_interval_seconds=21_600,
                    tags=[],
                )
                for position, user in enumerate(users)
            )
            session.add_all(subscriptions)
            session.add(
                SearchLeg(
                    search_query_id=query.id,
                    position=0,
                    origin_code="SHA",
                    destination_code="TYO",
                    departure_date=date(2026, 9, 1),
                )
            )
            await session.flush()
            user_ids = [user.id for user in users]
            query_id = query.id
            subscription_ids = [subscription.id for subscription in subscriptions]

        one_megabyte = 1_048_576
        admission_start = asyncio.Event()

        async def admission_attempt(position: int):
            await admission_start.wait()
            try:
                async with factory() as session, session.begin():
                    return await create_export_job(
                        session,
                        user_id=user_ids[position],
                        subscription_id=subscription_ids[position],
                        idempotency_key=f"global-admission-{position}-{suffix}",
                        export_format="json",
                        range_start=datetime(2026, 1, 1, tzinfo=UTC),
                        range_end=datetime(2026, 2, 1, tzinfo=UTC),
                        max_attempts=3,
                        max_active_jobs=5,
                        max_global_active_jobs=1,
                        max_manifest_runs=20_000,
                        max_file_bytes=one_megabyte,
                        max_retained_files=20,
                        max_retained_bytes=20 * one_megabyte,
                        dispatch_lease_seconds=300,
                    )
            except ExportJobBusyError as error:
                return error

        admission_tasks = [
            asyncio.create_task(admission_attempt(position)) for position in range(2)
        ]
        admission_start.set()
        admission_results = await asyncio.gather(*admission_tasks)
        assert sum(isinstance(result, tuple) for result in admission_results) == 1
        assert sum(isinstance(result, ExportJobBusyError) for result in admission_results) == 1
        async with factory() as session, session.begin():
            await session.execute(delete(ExportJob).where(ExportJob.user_id.in_(user_ids)))

        job_ids = []
        for position in range(2):
            async with factory() as session, session.begin():
                job, created = await create_export_job(
                    session,
                    user_id=user_ids[position],
                    subscription_id=subscription_ids[position],
                    idempotency_key=f"atomic-{position}-{suffix}",
                    export_format="json",
                    range_start=datetime(2026, 1, 1, tzinfo=UTC),
                    range_end=datetime(2026, 2, 1, tzinfo=UTC),
                    max_attempts=3,
                    max_active_jobs=5,
                    max_global_active_jobs=100,
                    max_manifest_runs=20_000,
                    max_file_bytes=one_megabyte,
                    max_retained_files=20,
                    max_retained_bytes=20 * one_megabyte,
                    dispatch_lease_seconds=300,
                )
                assert created is True
                assert job.reserved_bytes == 0
                job_ids.append(job.id)

        redispatch_time = datetime.now(UTC) + timedelta(seconds=301)
        async with factory() as session, session.begin():
            dispatch_batch = await lease_dispatchable_export_ids(
                session,
                limit=20,
                dispatch_lease_seconds=300,
                now=redispatch_time,
            )
        assert set(dispatch_batch.job_ids) == set(job_ids)
        async with factory() as session, session.begin():
            assert (
                await mark_export_dispatch_published(
                    session,
                    job_ids=(dispatch_batch.job_ids[0],),
                    leased_until=dispatch_batch.leased_until,
                    now=redispatch_time,
                )
                == 1
            )
        async with factory() as session, session.begin():
            immediate = await lease_dispatchable_export_ids(
                session,
                limit=20,
                dispatch_lease_seconds=300,
                now=redispatch_time,
            )
        assert immediate.job_ids == ()
        async with factory() as session, session.begin():
            retried = await lease_dispatchable_export_ids(
                session,
                limit=20,
                dispatch_lease_seconds=300,
                now=redispatch_time + timedelta(seconds=301),
            )
        assert retried.job_ids == (dispatch_batch.job_ids[1],)

        monkeypatch.setattr(
            "app.services.export_jobs.shutil.disk_usage",
            lambda _path: SimpleNamespace(
                total=10 * one_megabyte,
                used=8 * one_megabyte,
                free=2 * one_megabyte,
            ),
        )
        generation_started = asyncio.Event()
        release_generation = asyncio.Event()

        async def hold_generation(_session_factory, *, work, **_kwargs):
            generation_started.set()
            await release_generation.wait()
            return GeneratedExport(
                file_name=f"fare-export-{work.job_id}-{'0' * 32}.json",
                content_type="application/json; charset=utf-8",
                size_bytes=2,
                checksum_sha256="0" * 64,
                row_count=0,
            )

        monkeypatch.setattr("app.tasks.exports.generate_export_file", hold_generation)
        settings = Settings(
            export_directory=str(tmp_path),
            export_max_file_bytes=one_megabyte,
            export_min_free_bytes=one_megabyte,
            export_min_free_ratio=0,
            export_lease_seconds=60,
            export_retry_base_seconds=5,
        )
        attempts = [
            asyncio.create_task(
                run_export_job_once(
                    job_id,
                    settings=settings,
                    session_factory=factory,
                )
            )
            for job_id in job_ids
        ]
        await asyncio.wait_for(generation_started.wait(), timeout=2)
        done, _pending = await asyncio.wait(
            attempts,
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        release_generation.set()
        results = await asyncio.gather(*attempts)

        assert len(done) == 1
        assert sum(result["status"] == "succeeded" for result in results) == 1
        assert sum(result["status"] == "deferred" for result in results) == 1
        async with factory() as session:
            jobs = (await session.scalars(select(ExportJob).where(ExportJob.id.in_(job_ids)))).all()
        assert {job.status for job in jobs} == {"pending", "succeeded"}
        assert all(job.reserved_bytes == 0 for job in jobs)
    finally:
        if user_ids:
            async with factory() as session, session.begin():
                await session.execute(delete(User).where(User.id.in_(user_ids)))
                if query_id is not None:
                    await session.execute(delete(SearchQuery).where(SearchQuery.id == query_id))
        await engine.dispose()


async def test_storage_pressure_is_persisted_with_backoff_without_consuming_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                suffix = uuid4().hex
                user = User(
                    username=f"storage-defer-{suffix}",
                    normalized_username=f"storage-defer-{suffix}",
                    display_name="Storage defer",
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
                    normalized_query={"fixture": "storage-defer"},
                )
                session.add_all((user, query))
                await session.flush()
                subscription = Subscription(
                    user_id=user.id,
                    search_query_id=query.id,
                    name="Storage defer route",
                    enabled=True,
                    poll_interval_seconds=21_600,
                    tags=[],
                )
                session.add_all(
                    (
                        subscription,
                        SearchLeg(
                            search_query_id=query.id,
                            position=0,
                            origin_code="SHA",
                            destination_code="TYO",
                            departure_date=date(2026, 9, 1),
                        ),
                    )
                )
                await session.flush()
                one_megabyte = 1_048_576
                created_at = datetime.now(UTC)
                job = ExportJob(
                    user_id=user.id,
                    subscription_id=subscription.id,
                    search_query_id=query.id,
                    idempotency_key=f"storage-defer-{suffix}",
                    request_fingerprint=uuid4().hex + uuid4().hex,
                    format="json",
                    status=ExportStatus.PENDING.value,
                    range_start=datetime(2026, 1, 1, tzinfo=UTC),
                    range_end=datetime(2026, 2, 1, tzinfo=UTC),
                    snapshot_at=created_at,
                    attempt=0,
                    max_attempts=3,
                    reserved_bytes=one_megabyte,
                    available_at=created_at,
                    processed_rows=0,
                )
                session.add(job)
                await session.flush()
                job_id = job.id

            monkeypatch.setattr(
                "app.services.export_jobs.shutil.disk_usage",
                lambda _path: SimpleNamespace(
                    total=10 * one_megabyte,
                    used=9 * one_megabyte,
                    free=one_megabyte,
                ),
            )
            settings = Settings(
                export_directory=str(tmp_path),
                export_max_file_bytes=one_megabyte,
                export_min_free_bytes=one_megabyte,
                export_min_free_ratio=0,
                export_lease_seconds=60,
                export_retry_base_seconds=5,
            )
            before_run = datetime.now(UTC)
            result = await run_export_job_once(
                job_id,
                settings=settings,
                session_factory=factory,
            )

            assert result == {
                "job_id": str(job_id),
                "claimed": False,
                "status": "deferred",
                "error_code": "insufficient_export_storage",
            }
            async with factory() as session:
                stored = await session.get(ExportJob, job_id)
                assert stored is not None
                assert stored.status == ExportStatus.PENDING.value
                assert stored.attempt == 0
                assert stored.error_code == "insufficient_export_storage"
                assert stored.error_message == (
                    "Export storage is temporarily busy. The job will retry automatically."
                )
                assert stored.available_at >= before_run + timedelta(seconds=60)
                assert stored.lease_owner is None
                assert stored.lease_expires_at is None
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_hot_and_detached_archive_export_is_deduplicated_fenced_and_expired(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        setup = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            january = datetime(2099, 1, 5, 8, tzinfo=UTC)
            february = datetime(2099, 2, 6, 9, tzinfo=UTC)
            await connection.execute(text(price_observation_partition_ddl(january)))
            await connection.execute(text(price_observation_partition_ddl(february)))
            user, other_user, subscription, job = await _seed_export_graph(
                setup,
                january=january,
                february=february,
            )
            await setup.flush()
            # A catalog-allowlisted overlap proves hot/cold UNION deduplication.
            await connection.execute(
                text(
                    "CREATE TABLE farescope_archive.price_observations_y2099m01 "
                    "(LIKE public.price_observations INCLUDING DEFAULTS)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO farescope_archive.price_observations_y2099m01 "
                    "SELECT * FROM public.price_observations_y2099m01"
                )
            )
            # February follows the production detach + schema-move lifecycle exactly.
            await connection.execute(
                text(
                    "ALTER TABLE public.price_observations "
                    "DETACH PARTITION public.price_observations_y2099m02"
                )
            )
            await connection.execute(
                text("ALTER TABLE public.price_observations_y2099m02 SET SCHEMA farescope_archive")
            )

            page = await load_export_observation_page(
                setup,
                job_id=job.id,
                search_query_id=subscription.search_query_id,
                range_start=datetime(2099, 1, 1, tzinfo=UTC),
                range_end=datetime(2099, 3, 1, tzinfo=UTC),
                after_observed_at=None,
                after_id=None,
                limit=100,
            )
            assert [row.observed_at for row in page] == [january, february]
            assert len({row.id for row in page}) == 2

            first_page = await load_export_observation_page(
                setup,
                job_id=job.id,
                search_query_id=subscription.search_query_id,
                range_start=datetime(2099, 1, 1, tzinfo=UTC),
                range_end=datetime(2099, 3, 1, tzinfo=UTC),
                after_observed_at=None,
                after_id=None,
                limit=1,
            )
            second_page = await load_export_observation_page(
                setup,
                job_id=job.id,
                search_query_id=subscription.search_query_id,
                range_start=datetime(2099, 1, 1, tzinfo=UTC),
                range_end=datetime(2099, 3, 1, tzinfo=UTC),
                after_observed_at=first_page[-1].observed_at,
                after_id=first_page[-1].id,
                limit=1,
            )
            assert [first_page[0].observed_at, second_page[0].observed_at] == [
                january,
                february,
            ]

            await connection.execute(
                text("ALTER SCHEMA farescope_archive RENAME TO farescope_archive_hidden")
            )
            hot_only = await load_export_observation_page(
                setup,
                job_id=job.id,
                search_query_id=subscription.search_query_id,
                range_start=datetime(2099, 1, 1, tzinfo=UTC),
                range_end=datetime(2099, 3, 1, tzinfo=UTC),
                after_observed_at=None,
                after_id=None,
                limit=100,
            )
            assert [row.observed_at for row in hot_only] == [january]
            await connection.execute(
                text("ALTER SCHEMA farescope_archive_hidden RENAME TO farescope_archive")
            )
            await setup.close()

            factory = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            settings = Settings(
                export_directory=str(tmp_path),
                export_page_size=100,
                export_max_rows=1_000,
                export_max_file_bytes=1_048_576,
                export_file_ttl_seconds=3600,
                export_lease_seconds=60,
            )
            result = await run_export_job_once(
                job.id,
                settings=settings,
                session_factory=factory,
            )
            assert result["status"] == "succeeded"
            assert result["rows"] == 2

            async with factory() as verification:
                stored = await verification.get(ExportJob, job.id)
                assert stored is not None
                assert stored.status == ExportStatus.SUCCEEDED.value
                assert (
                    await verification.scalar(
                        select(func.count())
                        .select_from(ExportJobCollectionRun)
                        .where(ExportJobCollectionRun.export_job_id == job.id)
                    )
                ) == 0
                assert stored.file_name is not None
                assert stored.file_name.count("-") > str(job.id).count("-")
                file_path = tmp_path / stored.file_name
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                assert payload["export"]["scope"] == "canonical_query"
                assert payload["export"]["snapshot_at"] == job.snapshot_at.isoformat()
                assert [item["observed_at"] for item in payload["observations"]] == [
                    january.isoformat(),
                    february.isoformat(),
                ]
                assert "raw_payload" not in file_path.read_text(encoding="utf-8")
                assert file_path.stat().st_size == stored.size_bytes
                assert await get_owned_export_job(
                    verification,
                    user_id=user.id,
                    job_id=job.id,
                )
                with pytest.raises(ExportJobNotFoundError):
                    await get_owned_export_job(
                        verification,
                        user_id=other_user.id,
                        job_id=job.id,
                    )
                stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                csv_job = ExportJob(
                    user_id=user.id,
                    subscription_id=subscription.id,
                    search_query_id=subscription.search_query_id,
                    idempotency_key=f"csv-{uuid4().hex}",
                    request_fingerprint=uuid4().hex + uuid4().hex,
                    format="csv",
                    status=ExportStatus.PENDING.value,
                    range_start=datetime(2099, 1, 1, tzinfo=UTC),
                    range_end=datetime(2099, 3, 1, tzinfo=UTC),
                    snapshot_at=job.snapshot_at,
                    attempt=0,
                    max_attempts=3,
                    available_at=datetime.now(UTC),
                    processed_rows=0,
                )
                verification.add(csv_job)
                await verification.flush()
                manifest_run_ids = (
                    await verification.scalars(
                        select(CollectionRun.id).where(
                            CollectionRun.search_query_id == subscription.search_query_id,
                            CollectionRun.status == "succeeded",
                            CollectionRun.finished_at >= csv_job.range_start,
                            CollectionRun.finished_at < csv_job.range_end,
                        )
                    )
                ).all()
                verification.add_all(
                    ExportJobCollectionRun(
                        export_job_id=csv_job.id,
                        collection_run_id=run_id,
                    )
                    for run_id in manifest_run_ids
                )
                await verification.commit()

            csv_result = await run_export_job_once(
                csv_job.id,
                settings=settings,
                session_factory=factory,
            )
            assert csv_result["status"] == "succeeded"
            assert csv_result["rows"] == 2
            async with factory() as verification:
                stored_csv = await verification.get(ExportJob, csv_job.id)
                assert stored_csv is not None
                assert stored_csv.file_name is not None
                with (tmp_path / stored_csv.file_name).open(
                    encoding="utf-8",
                    newline="",
                ) as csv_file:
                    csv_rows = list(csv.DictReader(csv_file))
                assert [row["observed_at"] for row in csv_rows] == [
                    january.isoformat(),
                    february.isoformat(),
                ]
                assert [int(row["total_price_minor"]) for row in csv_rows] == [
                    120_000,
                    118_000,
                ]
                stored_csv.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await verification.commit()

            maintenance = await maintain_exports_once(
                settings=settings,
                session_factory=factory,
            )
            assert maintenance["expired"] == 2
            assert maintenance["removed"] == 2
            async with factory() as verification:
                expired = await verification.get(ExportJob, job.id)
                assert expired is not None
                assert expired.status == ExportStatus.EXPIRED.value
                assert expired.file_name is None
                expired_csv = await verification.get(ExportJob, csv_job.id)
                assert expired_csv is not None
                assert expired_csv.status == ExportStatus.EXPIRED.value
                assert expired_csv.file_name is None
            assert not list(tmp_path.iterdir())
        finally:
            await setup.close()
            await transaction.rollback()
    await engine.dispose()


async def _seed_export_graph(
    session: AsyncSession,
    *,
    january: datetime,
    february: datetime,
) -> tuple[User, User, Subscription, ExportJob]:
    suffix = uuid4().hex
    user = User(
        username=f"export-{suffix}",
        normalized_username=f"export-{suffix}",
        display_name="Export owner",
        role="member",
        status="active",
    )
    other_user = User(
        username=f"export-other-{suffix}",
        normalized_username=f"export-other-{suffix}",
        display_name="Other export owner",
        role="member",
        status="active",
    )
    provider = Provider(
        code=f"export-{suffix[:20]}",
        display_name="Export fixture provider",
        enabled=True,
    )
    query = SearchQuery(
        provider=provider.code,
        query_hash=uuid4().hex + uuid4().hex,
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="CNY",
        direct_only=False,
        normalized_query={"fixture": "export"},
    )
    session.add_all((user, other_user, provider, query))
    await session.flush()
    session.add(
        SearchLeg(
            search_query_id=query.id,
            position=0,
            origin_code="SHA",
            destination_code="TYO",
            departure_date=date(2099, 3, 10),
        )
    )
    subscription = Subscription(
        user_id=user.id,
        search_query_id=query.id,
        name="Archived export route",
        enabled=True,
        poll_interval_seconds=21_600,
        tags=[],
    )
    run = CollectionRun(
        search_query_id=query.id,
        provider_id=provider.id,
        idempotency_key=f"export:{suffix}",
        status="succeeded",
        attempt=1,
        max_attempts=3,
        scheduled_at=january,
        started_at=january,
        finished_at=february,
        run_metadata={"fixture": "export"},
    )
    session.add_all((subscription, run))
    await session.flush()
    itinerary = Itinerary(
        collection_run_id=run.id,
        search_query_id=query.id,
        provider_id=provider.id,
        fingerprint=f"itinerary-{suffix}",
        total_duration_minutes=180,
        stop_count=0,
        is_direct=True,
        leg_count=1,
        itinerary_metadata={},
    )
    session.add(itinerary)
    await session.flush()
    offer = FareOffer(
        collection_run_id=run.id,
        itinerary_id=itinerary.id,
        fingerprint=f"offer-{suffix}",
        cabin="economy",
        currency="CNY",
        total_price_minor=120_000,
        offer_metadata={},
    )
    session.add(offer)
    await session.flush()
    snapshot_at = datetime.now(UTC)
    session.add_all(
        (
            PriceObservation(
                observed_at=january,
                search_query_id=query.id,
                collection_run_id=run.id,
                itinerary_id=itinerary.id,
                fare_offer_id=offer.id,
                provider_id=provider.id,
                offer_fingerprint=f"january-{suffix}",
                currency="CNY",
                total_price_minor=120_000,
                is_lowest=True,
                is_direct=True,
                created_at=snapshot_at - timedelta(seconds=1),
            ),
            PriceObservation(
                observed_at=february,
                search_query_id=query.id,
                collection_run_id=run.id,
                itinerary_id=itinerary.id,
                fare_offer_id=offer.id,
                provider_id=provider.id,
                offer_fingerprint=f"february-{suffix}",
                currency="CNY",
                total_price_minor=118_000,
                is_lowest=True,
                is_direct=True,
                created_at=snapshot_at - timedelta(seconds=1),
            ),
        )
    )
    await session.flush()
    job = ExportJob(
        user_id=user.id,
        subscription_id=subscription.id,
        search_query_id=query.id,
        idempotency_key=f"export-job-{suffix}",
        request_fingerprint=uuid4().hex + uuid4().hex,
        format="json",
        status=ExportStatus.PENDING.value,
        range_start=datetime(2099, 1, 1, tzinfo=UTC),
        range_end=datetime(2099, 3, 1, tzinfo=UTC),
        snapshot_at=snapshot_at,
        attempt=0,
        max_attempts=3,
        available_at=datetime.now(UTC),
        processed_rows=0,
    )
    session.add(job)
    await session.flush()
    session.add(
        ExportJobCollectionRun(
            export_job_id=job.id,
            collection_run_id=run.id,
        )
    )
    await session.flush()
    return user, other_user, subscription, job


async def _add_succeeded_observation_run(
    session: AsyncSession,
    *,
    query: SearchQuery,
    provider: Provider,
    observed_at: datetime,
    suffix: str,
    price_minor: int,
) -> tuple[CollectionRun, PriceObservation]:
    run = CollectionRun(
        search_query_id=query.id,
        provider_id=provider.id,
        idempotency_key=f"manifest:{suffix}",
        status="succeeded",
        attempt=1,
        max_attempts=3,
        scheduled_at=observed_at,
        started_at=observed_at,
        finished_at=observed_at,
        run_metadata={"fixture": "manifest"},
    )
    session.add(run)
    await session.flush()
    itinerary = Itinerary(
        collection_run_id=run.id,
        search_query_id=query.id,
        provider_id=provider.id,
        fingerprint=f"itinerary-{suffix}",
        total_duration_minutes=180,
        stop_count=0,
        is_direct=True,
        leg_count=1,
        itinerary_metadata={},
    )
    session.add(itinerary)
    await session.flush()
    offer = FareOffer(
        collection_run_id=run.id,
        itinerary_id=itinerary.id,
        fingerprint=f"offer-{suffix}",
        cabin="economy",
        currency="CNY",
        total_price_minor=price_minor,
        offer_metadata={},
    )
    session.add(offer)
    await session.flush()
    observation = PriceObservation(
        observed_at=observed_at,
        search_query_id=query.id,
        collection_run_id=run.id,
        itinerary_id=itinerary.id,
        fare_offer_id=offer.id,
        provider_id=provider.id,
        offer_fingerprint=f"observation-{suffix}",
        currency="CNY",
        total_price_minor=price_minor,
        is_lowest=True,
        is_direct=True,
    )
    session.add(observation)
    await session.flush()
    return run, observation

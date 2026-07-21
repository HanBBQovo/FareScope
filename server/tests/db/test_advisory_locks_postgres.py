from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.advisory_locks import (
    acquire_observation_partition_shared_lock,
    try_acquire_observation_partition_exclusive_lock,
)
from app.services import daily_trends, export_data, export_jobs

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]

SharedLockAcquirer = Callable[[AsyncSession], Awaitable[None]]


@pytest.mark.parametrize(
    "acquire_shared",
    (
        pytest.param(
            daily_trends.acquire_observation_partition_shared_lock,
            id="daily-trend-maintenance",
        ),
        pytest.param(
            export_data.acquire_observation_partition_shared_lock,
            id="export-page-reader",
        ),
        pytest.param(
            export_jobs.acquire_observation_partition_shared_lock,
            id="export-job-admission",
        ),
    ),
)
async def test_shared_partition_lock_blocks_lifecycle_try_until_commit(
    acquire_shared: SharedLockAcquirer,
) -> None:
    assert DATABASE_URL is not None
    assert acquire_shared is acquire_observation_partition_shared_lock
    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as holder, factory() as lifecycle:
            async with holder.begin():
                await acquire_shared(holder)
                async with lifecycle.begin():
                    assert (
                        await try_acquire_observation_partition_exclusive_lock(lifecycle)
                        is False
                    )

            async with lifecycle.begin():
                assert (
                    await try_acquire_observation_partition_exclusive_lock(lifecycle)
                    is True
                )
    finally:
        await engine.dispose()

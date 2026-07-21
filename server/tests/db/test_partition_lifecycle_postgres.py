from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.partitions import (
    maintain_observation_partition_lifecycle,
    price_observation_partition_ddl,
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


async def test_partition_archive_is_non_destructive_and_purge_is_explicit() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    partition_name = "price_observations_y2010m01"
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(price_observation_partition_ddl(date(2010, 1, 1))))

            archive_actions = await maintain_observation_partition_lifecycle(
                connection,
                reference=date(2026, 7, 21),
                archive_after_months=24,
                max_actions=1,
            )

            assert [(item.action, item.partition_name) for item in archive_actions] == [
                ("archive", partition_name)
            ]
            assert await _relation_exists(connection, f"public.{partition_name}") is False
            assert await _relation_exists(connection, f"farescope_archive.{partition_name}") is True

            purge_actions = await maintain_observation_partition_lifecycle(
                connection,
                reference=date(2026, 7, 21),
                archive_after_months=24,
                purge_after_months=84,
                max_actions=1,
            )

            assert [(item.action, item.partition_name) for item in purge_actions] == [
                ("purge", partition_name)
            ]
            assert (
                await _relation_exists(connection, f"farescope_archive.{partition_name}") is False
            )
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _relation_exists(connection, qualified_name: str) -> bool:
    return (
        await connection.scalar(
            text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": qualified_name},
        )
    ) is True

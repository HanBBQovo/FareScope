import asyncio
import os

import asyncpg
import pytest

from performance.contracts import CRITICAL_INDEXES, PARTITIONED_TABLES

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _inspect_catalog(database_url: str) -> tuple[set[tuple[str, str]], dict[str, str]]:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        index_rows = await connection.fetch(
            """
            SELECT tablename, indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
            """
        )
        partition_rows = await connection.fetch(
            """
            SELECT relation.relname AS table_name, partition.partstrat::text AS partstrat
            FROM pg_partitioned_table AS partition
            JOIN pg_class AS relation ON relation.oid = partition.partrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
            """
        )
    finally:
        await connection.close()

    indexes = {(row["tablename"], row["indexname"]) for row in index_rows}
    partitions = {row["table_name"]: row["partstrat"] for row in partition_rows}
    return indexes, partitions


def test_migrated_postgres_catalog_matches_performance_contract() -> None:
    assert DATABASE_URL is not None
    indexes, partitions = asyncio.run(_inspect_catalog(DATABASE_URL))

    for table_name, expected_indexes in CRITICAL_INDEXES.items():
        for index_name in expected_indexes:
            assert (table_name, index_name) in indexes

    for table_name in PARTITIONED_TABLES:
        assert partitions.get(table_name) == "r"

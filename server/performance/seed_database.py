from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

import asyncpg

from performance.safety import (
    DISPOSABLE_CONFIRMATION,
    redact_url,
    require_confirmation,
    to_asyncpg_url,
    validate_performance_database_url,
)

SQL_PATH = Path(__file__).with_name("generate_load.sql")
_VARIABLE_PATTERN = re.compile(
    r"(?<!:):(?P<name>users|subscriptions_per_user|query_count|observations_per_query|"
    r"offers_per_query|queue_count|history_days)\b"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed generate_load.sql through asyncpg, without requiring a psql client."
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Required exact value: {DISPOSABLE_CONFIRMATION}",
    )
    parser.add_argument("--users", type=int, default=500)
    parser.add_argument("--subscriptions-per-user", type=int, default=12)
    parser.add_argument("--query-count", type=int, default=2_000)
    parser.add_argument("--observations-per-query", type=int, default=240)
    parser.add_argument("--offers-per-query", type=int, default=20)
    parser.add_argument("--queue-count", type=int, default=5_000)
    parser.add_argument("--history-days", type=int, default=21)
    return parser.parse_args()


def _validated_scale(arguments: argparse.Namespace) -> dict[str, int]:
    values = {
        "users": arguments.users,
        "subscriptions_per_user": arguments.subscriptions_per_user,
        "query_count": arguments.query_count,
        "observations_per_query": arguments.observations_per_query,
        "offers_per_query": arguments.offers_per_query,
        "queue_count": arguments.queue_count,
        "history_days": arguments.history_days,
    }
    if any(value <= 0 for value in values.values()):
        raise ValueError("all scale values must be positive")
    if not 1 <= values["offers_per_query"] <= 100:
        raise ValueError("offers_per_query must be between 1 and 100")
    if not 1 <= values["history_days"] <= 27:
        raise ValueError("history_days must be between 1 and 27")
    return values


def _render_sql(values: dict[str, int]) -> str:
    source = SQL_PATH.read_text()
    transaction_start = source.find("BEGIN;")
    if transaction_start < 0:
        raise RuntimeError("generate_load.sql no longer contains its expected BEGIN marker")
    body = source[transaction_start:]

    def replace(match: re.Match[str]) -> str:
        return str(values[match.group("name")])

    rendered = _VARIABLE_PATTERN.sub(replace, body)
    unresolved = sorted(set(_VARIABLE_PATTERN.findall(rendered)))
    if unresolved:
        raise RuntimeError(f"unresolved psql variables: {unresolved}")
    if "\\" in rendered:
        raise RuntimeError("psql meta-command found in executable SQL body")
    return rendered


async def _seed(database_url: str, values: dict[str, int]) -> dict[str, object]:
    connection = await asyncpg.connect(to_asyncpg_url(database_url))
    try:
        started = time.perf_counter()
        await connection.execute(_render_sql(values))
        elapsed_seconds = time.perf_counter() - started
        rows = await connection.fetch(
            """
            SELECT relation, rows
            FROM (
                SELECT 'users'::text AS relation, count(*)::bigint AS rows
                FROM users WHERE normalized_username LIKE 'perf-user-%'
                UNION ALL
                SELECT 'subscriptions', count(*) FROM subscriptions
                WHERE tags @> '["performance"]'::jsonb
                UNION ALL
                SELECT 'canonical searches', count(*) FROM search_queries
                WHERE normalized_query @> '{"perf_fixture": true}'::jsonb
                UNION ALL
                SELECT 'collection queue', count(*) FROM collection_runs
                WHERE idempotency_key LIKE 'perf:queue:%'
                UNION ALL
                SELECT 'fare offers', count(*) FROM fare_offers
                WHERE offer_metadata @> '{"perf_fixture": true}'::jsonb
                UNION ALL
                SELECT 'price observations', count(*) FROM price_observations
                WHERE offer_fingerprint LIKE 'perf:%'
            ) AS counts
            ORDER BY relation
            """
        )
        settings = await connection.fetchrow(
            """
            SELECT current_setting('server_version') AS server_version,
                   current_setting('shared_buffers') AS shared_buffers,
                   current_setting('work_mem') AS work_mem,
                   current_setting('effective_cache_size') AS effective_cache_size,
                   current_setting('max_connections') AS max_connections
            """
        )
        database_size = await connection.fetchval(
            "SELECT pg_database_size(current_database())"
        )
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        return {
            "database_url": redact_url(database_url),
            "database_size_bytes": database_size,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "rows": {row["relation"]: row["rows"] for row in rows},
            "scale": values,
            "schema_revision": revision,
            "postgresql": dict(settings) if settings else None,
        }
    finally:
        await connection.close()


async def main() -> None:
    arguments = _arguments()
    require_confirmation(arguments.confirm)
    values = _validated_scale(arguments)
    database_url = validate_performance_database_url(
        os.environ["FARESCOPE_PERF_DATABASE_URL"]
    )
    result = await _seed(database_url, values)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

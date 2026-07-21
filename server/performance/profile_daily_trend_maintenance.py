from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta

import asyncpg

from performance.safety import to_asyncpg_url, validate_performance_database_url

CATALOG_DISCOVERY_SQL = """
SELECT subscriptions.search_query_id
FROM subscriptions
GROUP BY subscriptions.search_query_id
ORDER BY subscriptions.search_query_id
"""

LEGACY_RAW_DISCOVERY_SQL = """
SELECT price_observations.search_query_id
FROM price_observations
WHERE price_observations.observed_at >= $1
  AND price_observations.observed_at < $2
  AND price_observations.is_lowest IS TRUE
GROUP BY price_observations.search_query_id
ORDER BY price_observations.search_query_id
"""

CANDIDATE_PAGE_SQL = """
WITH tracked_queries AS (
    SELECT subscriptions.search_query_id
    FROM subscriptions
    GROUP BY subscriptions.search_query_id
), calendar_days AS (
    SELECT generate_series($1::date, $2::date, interval '1 day')::date AS observation_date
)
SELECT tracked_queries.search_query_id, calendar_days.observation_date
FROM tracked_queries
CROSS JOIN calendar_days
ORDER BY calendar_days.observation_date, tracked_queries.search_query_id
LIMIT 500
"""


async def _plan(connection: asyncpg.Connection, statement: str, *parameters: object) -> dict:
    row = await connection.fetchrow(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
        *parameters,
    )
    value = row[0]
    payload = json.loads(value) if isinstance(value, str) else value
    result = payload[0]
    root = result["Plan"]
    return {
        "planning_ms": round(result["Planning Time"], 3),
        "execution_ms": round(result["Execution Time"], 3),
        "rows": root["Actual Rows"],
        "shared_hits": root.get("Shared Hit Blocks", 0),
        "shared_reads": root.get("Shared Read Blocks", 0),
    }


async def main() -> None:
    database_url = validate_performance_database_url(os.environ["FARESCOPE_PERF_DATABASE_URL"])
    connection = await asyncpg.connect(to_asyncpg_url(database_url))
    try:
        anchor = await connection.fetchval(
            "SELECT max(observed_at) FROM price_observations WHERE offer_fingerprint LIKE 'perf:%'"
        )
        if anchor is None:
            raise RuntimeError("performance fixture has no observations")
        range_end = anchor + timedelta(days=1)
        range_start = range_end - timedelta(days=30)
        result = {
            "price_observations": await connection.fetchval(
                "SELECT count(*) FROM price_observations WHERE offer_fingerprint LIKE 'perf:%'"
            ),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "batch_size": 500,
            "subscription_catalog_discovery": await _plan(
                connection,
                CATALOG_DISCOVERY_SQL,
            ),
            "legacy_raw_discovery": await _plan(
                connection,
                LEGACY_RAW_DISCOVERY_SQL,
                range_start,
                range_end,
            ),
            "candidate_page": await _plan(
                connection,
                CANDIDATE_PAGE_SQL,
                range_start.date(),
                (range_end - timedelta(days=1)).date(),
            ),
        }
    finally:
        await connection.close()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

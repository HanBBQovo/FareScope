from __future__ import annotations

import asyncio
import json
import os

import asyncpg

from performance.safety import to_asyncpg_url, validate_performance_database_url

VERIFY_SQL = """
WITH run_minima AS (
    SELECT
        search_query_id,
        (observed_at AT TIME ZONE 'UTC')::date AS observation_date,
        currency,
        false AS direct_only,
        collection_run_id,
        observed_at,
        min(total_price_minor) AS price_minor
    FROM price_observations
    WHERE is_lowest IS TRUE
    GROUP BY
        search_query_id,
        (observed_at AT TIME ZONE 'UTC')::date,
        currency,
        collection_run_id,
        observed_at
    UNION ALL
    SELECT
        search_query_id,
        (observed_at AT TIME ZONE 'UTC')::date AS observation_date,
        currency,
        true AS direct_only,
        collection_run_id,
        observed_at,
        min(total_price_minor) AS price_minor
    FROM price_observations
    WHERE is_lowest IS TRUE AND is_direct IS TRUE
    GROUP BY
        search_query_id,
        (observed_at AT TIME ZONE 'UTC')::date,
        currency,
        collection_run_id,
        observed_at
), expected AS (
    SELECT
        search_query_id,
        observation_date,
        currency,
        direct_only,
        min(price_minor) AS lowest_price_minor,
        max(price_minor) AS highest_price_minor,
        sum(price_minor) AS price_sum_minor,
        count(*) AS sample_count,
        min(observed_at) AS first_observed_at,
        max(observed_at) AS last_observed_at
    FROM run_minima
    GROUP BY search_query_id, observation_date, currency, direct_only
), differences AS (
    SELECT 1
    FROM expected
    FULL OUTER JOIN daily_trend_aggregates AS actual
      USING (search_query_id, observation_date, currency, direct_only)
    WHERE expected.search_query_id IS NULL
       OR actual.search_query_id IS NULL
       OR expected.lowest_price_minor IS DISTINCT FROM actual.lowest_price_minor
       OR expected.highest_price_minor IS DISTINCT FROM actual.highest_price_minor
       OR expected.price_sum_minor IS DISTINCT FROM actual.price_sum_minor
       OR expected.sample_count IS DISTINCT FROM actual.sample_count
       OR expected.first_observed_at IS DISTINCT FROM actual.first_observed_at
       OR expected.last_observed_at IS DISTINCT FROM actual.last_observed_at
), performance_queries AS (
    SELECT search_query_id
    FROM price_observations
    WHERE offer_fingerprint LIKE 'perf:%'
    GROUP BY search_query_id
), coverage_anchor AS (
    SELECT max((observed_at AT TIME ZONE 'UTC')::date) AS end_date
    FROM price_observations
    WHERE offer_fingerprint LIKE 'perf:%'
), coverage_calendar AS (
    SELECT generate_series(
        coverage_anchor.end_date - 89,
        coverage_anchor.end_date,
        interval '1 day'
    )::date AS observation_date
    FROM coverage_anchor
    WHERE coverage_anchor.end_date IS NOT NULL
), source_coverage AS (
    SELECT
        search_query_id,
        (observed_at AT TIME ZONE 'UTC')::date AS observation_date,
        max(observed_at) AS source_last_observed_at
    FROM price_observations
    WHERE offer_fingerprint LIKE 'perf:%'
      AND is_lowest IS TRUE
    GROUP BY search_query_id, (observed_at AT TIME ZONE 'UTC')::date
), expected_coverage AS (
    SELECT
        performance_queries.search_query_id,
        coverage_calendar.observation_date,
        source_coverage.source_last_observed_at
    FROM performance_queries
    CROSS JOIN coverage_calendar
    LEFT JOIN source_coverage
      USING (search_query_id, observation_date)
), coverage_differences AS (
    SELECT 1
    FROM expected_coverage
    FULL OUTER JOIN daily_trend_aggregate_coverage AS actual
      USING (search_query_id, observation_date)
    WHERE expected_coverage.search_query_id IS NULL
       OR actual.search_query_id IS NULL
       OR expected_coverage.source_last_observed_at
          IS DISTINCT FROM actual.source_last_observed_at
)
SELECT
    (SELECT count(*) FROM expected) AS expected_aggregates,
    (SELECT count(*) FROM daily_trend_aggregates) AS actual_aggregates,
    (SELECT count(*) FROM differences) AS aggregate_differences,
    (SELECT count(*) FROM expected_coverage) AS expected_coverage,
    (SELECT count(*) FROM daily_trend_aggregate_coverage) AS actual_coverage,
    (SELECT count(*) FROM coverage_differences) AS coverage_differences
"""


async def main() -> None:
    database_url = validate_performance_database_url(os.environ["FARESCOPE_PERF_DATABASE_URL"])
    connection = await asyncpg.connect(to_asyncpg_url(database_url))
    try:
        row = await connection.fetchrow(VERIFY_SQL)
    finally:
        await connection.close()
    result = dict(row) if row is not None else {}
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("aggregate_differences") or result.get("coverage_differences"):
        raise SystemExit("daily trend aggregate verification failed")


if __name__ == "__main__":
    asyncio.run(main())

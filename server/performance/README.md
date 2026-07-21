# PostgreSQL workload and plan checks

This directory contains the executable performance baseline for FareScope. It is intentionally not
part of ordinary CI because it creates hundreds of thousands or millions of rows and requires a
real PostgreSQL planner. Ordinary CI runs structural contracts under `tests/performance/`.

## Safety

Use a disposable database created from the current Alembic revision. The generator refuses to run
without an explicit confirmation string and tags generated rows with `perf:` identifiers or
`@example.invalid` addresses. It does not truncate application tables, but benchmark rows are not
production data and must never be inserted into a production database.

The default 21-day observation horizon fits the current and previous monthly partitions created by
the initial migration. If the horizon changes, create all required partitions before seeding.

## Run

From `server/`, with a migrated disposable database:

```bash
export FARESCOPE_PERF_DATABASE_URL='postgresql://farescope:farescope@localhost:5432/farescope_perf'

psql "$FARESCOPE_PERF_DATABASE_URL" \
  -v perf_confirm=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE \
  -f performance/generate_load.sql

psql "$FARESCOPE_PERF_DATABASE_URL" \
  -f performance/explain_hot_queries.sql \
  | tee performance-plan.txt
```

The defaults create the reference profile: 500 users, 6,000 subscriptions with persisted local
filters, 2,000 canonical searches, 40,000 itineraries/offers, roughly 72,000 segments, 60,000
latest calendar rows, 480,000 price observations, and 5,000 queue rows. Each successful query run
has 20 price-ordered offers with mixed airlines and direct/transfer shapes; round trips have both
outbound and inbound segments.

Override psql variables for smoke or large profiles:

```bash
psql "$FARESCOPE_PERF_DATABASE_URL" \
  -v perf_confirm=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE \
  -v users=5000 \
  -v subscriptions_per_user=20 \
  -v query_count=20000 \
  -v offers_per_query=20 \
  -v observations_per_query=720 \
  -v queue_count=50000 \
  -f performance/generate_load.sql
```

To profile the SQL emitted by the current service implementation rather than only the static SQL
reference queries, run:

```bash
export FARESCOPE_PERF_DATABASE_URL='postgresql://farescope:farescope@localhost:5432/farescope_perf'
uv run python performance/profile_fare_queries.py
```

The profiler loads one generated user's visible subscriptions, captures the real SQLAlchemy
statements for subscription lists, latest filtered fares, exact fare totals, offer pages, segment
hydration, raw/daily history, one-way/round-trip calendars, dashboard trends/counts, and collection
health, then reruns every captured read with `EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON)`.
It is read-only, but `EXPLAIN ANALYZE` executes each query. Run it three times for warm-cache ranges.

Run plan output at least three times after seeding. The first run is cold-cache evidence; the next
three runs are the warm measurements used for SLO comparison.

## Acceptance checklist

- Save `SELECT version()` and relevant settings such as `shared_buffers`, `work_mem`,
  `effective_cache_size`, `random_page_cost`, and `max_connections` with the report.
- Save row counts and relation/index sizes for each hot table.
- Confirm bounded price history prunes unrelated monthly partitions.
- Confirm selective reads do not sequentially scan high-cardinality tables.
- Confirm no external disk sort occurs at page size 50 or lease batch size 100.
- Confirm the same query plan is stable across three warm runs.
- Record planning time, execution time, shared buffer hits/reads, and returned rows.
- Compare index size and write cost before accepting any covering index.

The initial schema is expected to expose improvement opportunities for the full keyset order. The
target indexes and SLOs are documented in `docs/PERFORMANCE.md`; a plan regression should result in
a measured migration, not an ad hoc production index.

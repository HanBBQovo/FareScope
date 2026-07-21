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

All commands below run from `server/`. The Python path is the preferred workflow when the host has
no `psql` client. It creates a new database but never drops or reuses one, and both the name prefix
and confirmation string are mandatory:

```bash
export FARESCOPE_PERF_ADMIN_URL='postgresql://farescope:farescope@127.0.0.1:5432/postgres'
uv run python -m performance.create_database farescope_perf_reference_20260721 \
  --confirm I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE

export FARESCOPE_PERF_DATABASE_URL='postgresql://farescope:farescope@127.0.0.1:5432/farescope_perf_reference_20260721'
export FARESCOPE_DATABASE_URL='postgresql+asyncpg://farescope:farescope@127.0.0.1:5432/farescope_perf_reference_20260721'
uv run alembic upgrade head
uv run python -m performance.seed_database \
  --confirm I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE
```

`seed_database.py` executes the same checked-in `generate_load.sql` through asyncpg. The psql
workflow remains available for environments that provide the client:

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

Run the concurrent service and full in-process ASGI API matrix with:

```bash
uv run python -m performance.concurrent_benchmark \
  --confirm I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE \
  --concurrency 1,8,16,32 \
  --requests-per-scenario 80 \
  --warmup-requests 4 \
  --users 64 \
  --client-cold-probe \
  --output performance/results/reference-concurrency.json
```

The matrix covers a 100-route dashboard, fare search, daily price history, calendar prices,
subscription lists, collection-run lists, and collection operations. It records p50/p95/p99,
throughput, status/error counts, SQL statement timing, private QueuePool acquisition timing, peak
checked-out connections, and database/environment metadata. The QueuePool hook is intentionally
performance-only and uses SQLAlchemy's private `_do_get` method; rerun the smoke matrix after a
SQLAlchemy upgrade.

The API layer executes authentication, ownership checks, database reads, Pydantic serialization,
and middleware through `httpx.ASGITransport`. It does not include a TCP socket, Uvicorn scheduling,
TLS, or reverse-proxy latency. Fare search uses an existing successful fixture run and stubs only
canonical-search creation plus collector dispatch, so no provider request or Celery publish occurs.
Collection operations executes the real status/schema SQL but stubs Redis `LLEN` to an immediate
unavailable result; Redis latency is a separate operational benchmark.

To run a scale larger than reference without claiming the 14.4-million-row large profile:

```bash
uv run python -m performance.seed_database \
  --confirm I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE \
  --observations-per-query 720
```

With 2,000 searches this produces 1.44 million observations, or 3x reference. Use a fresh database;
rerunning a different scale in an already-seeded database keeps existing deterministic rows and is
not an equivalent fresh distribution.

Run plan output at least three times after seeding. A fresh SQLAlchemy client pool is only
client/pool-cold evidence. It is not a PostgreSQL or operating-system cold-cache run: true cold
evidence requires a dedicated PostgreSQL instance whose shared buffers can be reset and a host on
which the page cache can be safely evicted. Never restart or purge a shared developer/production
server to manufacture a cold-cache number.

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

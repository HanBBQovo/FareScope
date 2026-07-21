# FareScope performance baseline

This document defines the performance contract for the modular monolith. It is a design
baseline, not a claim that production scale has already been proven. The executable PostgreSQL
workload under `server/performance/` is the source of truth for future measurements.

## Status labels

| Label | Meaning |
| --- | --- |
| `verified` | Enforced by an ordinary CI test or directly exercised in the current repository. |
| `needs scale verification` | The design and benchmark exist, but must be measured against a PostgreSQL dataset of the stated size. |
| `history dependent` | The result becomes meaningful only after enough real observations accumulate. |
| `blocked` | A required production input or environment is not yet available. |

Current status:

- `verified`: the observation table is range partitioned by UTC observation time; minimum hot-path
  indexes and bounded benchmark queries are covered by structure tests.
- `verified`: API, scheduler, collector, analysis, and notification responsibilities are separated
  at the process boundary in the architecture contract.
- `verified locally`: the repeatable reference concurrency workload, set-based 100-route dashboard
  reads, and zero-error completion across all 1,120 reference service/API requests.
- `verified locally`: maintained exact UTC-day Dashboard aggregates, bounded/resumable backfill,
  incomplete-coverage fallback, and raw-versus-aggregate equivalence at 480,000 and 1.44 million
  observations.
- `needs scale verification`: latency SLOs, connection budgets, index-only behavior, cache hit
  ratios, autovacuum settings, true cold-cache behavior, and the 14.4-million-row profile.
- `history dependent`: production price distributions and long-term aggregate retention costs.
- `blocked`: production hardware sizing and a representative server network environment.

### Local fare-query proof (2026-07-20)

The current generator and service-level SQL profiler were executed on PostgreSQL 16.13 Alpine
ARM64 with 500 users, 6,000 subscriptions and matching filter rows, 2,000 canonical searches, 7,000
collection runs, 40,000 itineraries, 40,000 offers, 71,982 segments, 60,000 latest calendar rows,
and 480,000 observations. The partitioned observations occupied 225 MB; offers, itineraries,
segments, and latest calendar snapshots occupied 21 MB, 13 MB, 21 MB, and 20 MB respectively.

Three warm runs of `performance/profile_fare_queries.py` captured the SQL emitted by the current
services and then executed every statement with `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)`. The ranges
below are PostgreSQL execution time summed across the statements that form one service operation;
they exclude network and response serialization.

| Operation | Shape | Warm execution range |
| --- | --- | ---: |
| Subscription list | joined page plus batched legs | 0.357-0.615 ms |
| Latest filtered fares | latest successful runs plus 12 filter branches | 1.465-2.404 ms |
| Fare search | exact total, keyset page, batched segments | 0.217-0.630 ms |
| Raw history | bounded summary plus 51-row page | 0.258-0.452 ms |
| Daily history | bounded summary plus 22 buckets | 0.259-0.417 ms |
| One-way calendar | 30 date rows | 0.080-0.126 ms |
| Round-trip matrix | 30 date pairs | 0.029-0.036 ms |
| Dashboard trend | 30 days across 12 filtered routes | 7.965-9.511 ms |
| Dashboard counts | exact active subscription and route counts | 0.022-0.040 ms |
| Collection health | last success, 24-hour rate, next due | 0.263-0.369 ms |
| Collection-run list | 21-row user keyset page | 0.199 ms |

All warm runs used shared-buffer hits, bounded sorts stayed in memory, and history pruned unrelated
monthly partitions. No new index was justified by the reference profile. At the dashboard hard
limit of 100 distinct route/filter contexts, latest-fare SQL executed in 8.902 ms with 13.866 ms of
planning and trend SQL executed in 31.633 ms with 16.212 ms of planning. Service calls took 109 ms
and 127 ms respectively on the local process. This remains below the historical-read SLO, but the
linear SQL construction/planning cost is a residual risk; do not raise the 100-route dashboard
limit without replacing branch unions with a set-based read model or measured cache.

These results are `verified` for local execution, schema compatibility, keyset behavior, and the
reference data shape. They are not production capacity evidence: the 14.4-million-row large profile,
cold storage behavior, concurrent clients, and production hardware still need scale verification.

### Local concurrent API proof and dashboard correction (2026-07-21)

A repeatable two-layer benchmark was run against a newly created disposable database pinned to
schema revision `20260720_0011`. The reference fixture contained 500 users, 6,088 subscriptions
(including one exact 100-route dashboard user), 2,000 canonical searches, 7,000 collection runs,
40,000 offers, 60,000 calendar prices, 200 provider schema observations, and 480,000 price
observations. The database occupied 341 MB.

The host was an Apple M5 MacBook Pro with 10 logical cores and 32 GB RAM. PostgreSQL 16.13 ARM64
used `shared_buffers=128MB`, `work_mem=4MB`, `effective_cache_size=4GB`, and
`max_connections=100`. Each layer used the configured API pool of 8 persistent plus 4 overflow
connections with a 2-second timeout. Every scenario used 64 authenticated fixture identities, four
warmups, and 80 measured requests. API measurements use the full FastAPI ASGI stack but exclude
socket, TLS, Uvicorn, and reverse-proxy cost.

The first reference run exposed a real capacity failure isolated to the 100-route dashboard. The
service built 100 independent latest-fare UNION branches and 100 independent trend branches. At
concurrency 32 this held all 12 connections long enough to produce QueuePool timeouts. The read was
replaced with typed filter `VALUES` sets: latest successful runs and filtered offers use one
set-based LATERAL statement, while trend observations use at most two LATERAL branches (simple and
itinerary-filtered). No pool increase or response cache was used.

| Layer / concurrency | Version | p50 | p95 | p99 | Throughput | Pool wait p95 | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Service / 16 | Before | 820 ms | 1,411 ms | 1,565 ms | 15.7 rps | 681 ms | 0/80 |
| Service / 16 | Set-based | 699 ms | 1,438 ms | 1,811 ms | 20.2 rps | 799 ms | 0/80 |
| Service / 32 | Before | 1,575 ms | 2,237 ms | 2,719 ms | 17.2 rps | 2,014 ms | 11/80 |
| Service / 32 | Set-based | 1,211 ms | 1,634 ms | 2,118 ms | 24.1 rps | 1,057 ms | 0/80 |
| ASGI API / 16 | Before | 1,111 ms | 1,387 ms | 1,475 ms | 13.9 rps | 344 ms | 0/80 |
| ASGI API / 16 | Set-based | 606 ms | 812 ms | 862 ms | 24.9 rps | 221 ms | 0/80 |
| ASGI API / 32 | Before | 1,579 ms | 2,691 ms | 2,919 ms | 15.5 rps | 1,368 ms | 4/80 |
| ASGI API / 32 | Set-based | 1,162 ms | 1,655 ms | 2,475 ms | 23.4 rps | 784 ms | 0/80 |

Concurrency timing is noisy on a shared laptop, so the acceptance signal is the repeated removal of
pool timeouts plus the large API throughput/tail improvement, not a claim that every percentile is
monotonic. A later all-workload c32 repeat also completed all 1,120 service/API requests without an
error. Its non-dashboard API p95 values were 148 ms for fare search, 314 ms for history, 240 ms for
calendar, 142 ms for subscriptions, 110 ms for collection runs, and 120 ms for collection
operations. History/calendar include a single-context latest-fare read and remained below their
documented 350 ms historical-page p95 target in this local ASGI setup.

Three optimized warm plan runs showed:

| 100-route operation | Application range | PostgreSQL execution | PostgreSQL planning |
| --- | ---: | ---: | ---: |
| Latest filtered fares | 31.9-39.1 ms | 0.685-0.793 ms | 1.750-2.098 ms |
| Dashboard trend | 44.6-54.4 ms | 21.921-24.615 ms | 1.154-1.322 ms |
| Collection run-status counts | 1.7-2.3 ms | 0.587-0.621 ms | 0.098-0.108 ms |
| Provider schema signals | 2.1-2.2 ms | 0.285-0.302 ms | 0.283-0.304 ms |

The directly comparable pre-change plan was 7.142 ms execution plus 8.029 ms planning for latest
fares and 19.528 ms plus 9.664 ms for trend. The set-based trend trades a small execution increase
for far lower construction/planning time and bounded statement shape. A PostgreSQL fixture test
verifies batched results against singleton results across airline, airport, price, stop, duration,
and departure-time filters.

Client/pool-cold evidence after the change was 116 ms for the first dashboard call on a fresh
engine versus 63 ms for the second call on the same pool. This is explicitly not PostgreSQL or OS
cold-cache evidence: the shared PostgreSQL server was not restarted and host caches were not
purged.

An additional fresh database used 720 observations for each of the same 2,000 searches: 1.44
million observations (3x reference), 861 MB, still revision `20260720_0011`. This is not the 14.4
million large profile. At c32 the API completed 80/80 dashboard requests with p95 1,990 ms and p99
2,365 ms, but the direct service layer had one 2-second pool timeout. Its dashboard trend alone
executed in 125 ms and touched 122,476 shared buffers. This is the remaining growth boundary: the
set-based change removes branch-planning collapse, but raw trend work still scales with observation
volume. The maintained model below addresses the compatible-filter portion of that boundary; the
14.4-million-row profile still requires production-like dedicated hardware.

### Maintained daily Dashboard trends (2026-07-21)

Revision `20260721_0013` implements the bounded aggregate/read model identified above. It adds
`daily_trend_aggregates` and a separate `daily_trend_aggregate_coverage` watermark. The migration
only creates schema and indexes; it deliberately does not scan observation partitions while DDL is
running.

Each aggregate is keyed by canonical search, UTC observation date, currency, and direct-only mode.
It summarizes the exact per-collection-run minimum using integer minor units and retains minimum,
maximum, sum, sample count, and first/last observation timestamps. This permits an exact weighted
average across days. `daily_price_aggregates.service_date` is a travel date and is intentionally not
reused for an observation-time trend.

The Dashboard uses this path only when no airline, origin/destination airport, maximum price,
maximum stops, maximum duration, or departure-time filter is active. Direct-only is an explicit
aggregate dimension. All detail-filtered contexts preserve the original bounded observation and
itinerary path. For compatible contexts, every complete UTC day in the requested range must have a
coverage row, including days with no observations. Complete ranges use daily rows plus exact raw
partial-day boundaries; any missing coverage makes that context fall back to the complete raw
range. The current/previous 24-hour comparison always uses the last 48 hours of raw observations.
Consequently an interrupted backfill can cost more, but it cannot silently omit prices.

Successful collection persistence rebuilds the affected canonical query/day in the same database
transaction. Transaction advisory locks are acquired in deterministic date/query order, and the
operation deletes and reconstructs exact rows, so collection retries, corrected rows, and
maintenance reruns cannot additively drift. Maintenance also takes the observation-partition
shared lock and proves that every requested month is still attached to
`public.price_observations` before changing any aggregate. A detached archive overlap, or a month
that is neither attached nor archived, produces `blocked_source_unavailable` and preserves all
existing aggregate and coverage rows. It never interprets unavailable source data as an empty day.

The crash-safe default rebuilds 30 days, which fits the bootstrap previous/current-month hot
partition window, in 500-key transactions. Each committed batch is printed immediately as one JSON
line. `--checkpoint-file` atomically replaces a versioned cursor after commit and automatically
resumes it on the next invocation:

```bash
uv run python -m app.maintenance.daily_trends \
  --checkpoint-file var/daily-trends-30d.json
```

If the process commits and crashes before replacing the file, the prior cursor causes at most one
idempotent batch replay; it cannot skip a batch. The checkpoint is bound to start date, end date,
and optional search query, so a stale file from another scope is rejected. Operators capturing the
JSONL stream without a checkpoint may resume manually with both cursor fields:

```bash
uv run python -m app.maintenance.daily_trends \
  --days 30 \
  --batch-size 500 \
  --max-batches 200 \
  --after-date 2026-07-01 \
  --after-search-query-id 00000000-0000-0000-0000-000000000000
```

Ranges longer than 30 days are explicit historical operations. Every overlapping monthly source
partition must first be restored or remain attached; archived or purged months are rejected rather
than silently truncated. A PostgreSQL detached-partition test verifies that an existing historical
aggregate and its non-NULL source watermark remain unchanged after the refusal.

The disposable reference and 3x databases were rebuilt for 2,000 searches over 90 dates. Each
created 180,000 coverage rows, including empty dates, and 87,000 non-empty aggregate rows from
44,000 source query-days. The strict verifier now constructs the complete 2,000-by-90 expected
calendar: both databases report `expected_coverage=actual_coverage=180000`, zero missing or extra
rows, exact NULL empty-day watermarks, and zero aggregate differences. A PostgreSQL fixture
also verifies incomplete fallback, complete coverage, all current filter categories, partial UTC
boundaries, and a repeated idempotent rebuild.

Candidate discovery no longer rescans the full observation range for every page. It reads distinct
canonical queries from the subscription catalog, then keyset-pages query/date pairs. With the
default 500-key page, warm plans were stable across raw scale:

| Candidate operation | 480,000 observations | 1.44 million observations |
| --- | ---: | ---: |
| Subscription discovery | 1.152 ms / 133 hits | 0.846 ms / 133 hits |
| 500-key query/day page | 17.215 ms / 133 hits | 17.113 ms / 133 hits |
| Removed raw discovery | 27.405 ms / 8,921 reads | 76.912 ms / 27,608 reads |

An actual 30-day rebuild of 60,000 query-days used 121 commits, wrote 87,000 aggregate rows, and
took 10.3 seconds at reference scale and 11.6 seconds at 3x. Restarting the completed checkpoint
returned the summary in 0.4 seconds without database maintenance work.

Warm 100-route plans improved as follows. The fixture contains 91 aggregate-compatible contexts;
the remaining detail-filtered contexts intentionally dominate the residual raw work.

| Dataset | Raw execution | Maintained execution | Change | Planning before/after | Shared hits before/after |
| --- | ---: | ---: | ---: | ---: | ---: |
| 480,000 observations | 24.637 ms | 13.992 ms | -43.2% | 1.499 / 4.199 ms | 20,985 / 22,701 |
| 1.44 million observations | 62.713 ms | 28.513 ms | -54.5% | 1.230 / 2.612 ms | 60,195 / 61,474 |

The compatible daily branch itself stayed effectively constant at about 1.1 ms and 565-566 shared
hits across both scales. Total shared hits remain volume-sensitive because complex filters and the
rolling 48-hour comparison still read raw observations. Planning is also higher for the explicit
coverage/fallback branches. These are intentional correctness costs and the next optimization
boundary if real users create many detail-filtered subscriptions.

The benchmark now requires an explicit, recorded `daily_trend_mode` of `aggregate` or `raw` for
reproducible A/B output. In adjacent 1.44-million-row c32 runs, aggregate versus raw Service was
20.39 versus 18.14 rps, p50 1,432 versus 1,620 ms, and p95 2,074 versus 2,210 ms. Full ASGI was
18.96 versus 15.59 rps, p50 1,312 versus 1,647 ms, and p95 2,323 versus 2,857 ms. Pool timeouts did
not improve monotonically. Reference-scale adjacent runs also reversed the throughput ordering
while other local workloads were active. The execution-plan reduction and exact verifier therefore
remain the acceptance evidence; concurrency results are not production capacity claims.
PostgreSQL/OS cold caches were not reset.

## Runtime isolation

FareScope remains a modular monolith, but each workload runs as a separate process type:

| Process | Responsibilities | Must not do |
| --- | --- | --- |
| API | Authentication, ownership checks, bounded reads/writes, SSE fan-out | Launch browsers, run large exports, perform long analytics |
| Scheduler | Find due canonical searches, create idempotent jobs, recover expired leases | Hold a database transaction while waiting on Redis or a provider |
| Collector | Playwright/provider access, normalize artifacts, persist one collection result | Share API worker memory or hold a connection while browsing |
| Analysis | Aggregates, trend windows, alert evaluation | Run CPU-heavy work on the API event loop |
| Notification | Deliveries, retries, provider-specific throttles | Recompute price history for every recipient |
| Export | Frozen-manifest CSV/JSON generation, hot/archive keyset reads, leases, and file cleanup | Share general worker concurrency or hold a database transaction while writing a file |

Python is appropriate for these I/O-heavy boundaries when the API uses async database access and
CPU-heavy work stays in worker processes or PostgreSQL aggregates. Horizontal API scaling is by
process/container. Browser concurrency is scaled independently and is never coupled to API worker
count.

Every database transaction must finish before any browser request, notification request, long Redis
wait, or export file write. A collector may keep normalized data in memory and then open one short
persistence transaction. An export reads one keyset page under the partition shared lock, closes
that transaction, writes the page, and records its lease heartbeat in a separate short transaction.

## Connection budget

The initial production budget assumes PostgreSQL `max_connections = 200`, with 30 connections
reserved for administration, migrations, monitoring, and incident response. Application processes
must stay below 120 direct client connections, leaving at least 50 connections of safety margin.

| Workload | Initial process count | SQLAlchemy pool per process | Overflow | Maximum clients |
| --- | ---: | ---: | ---: | ---: |
| API | 4 | 8 | 4 | 48 |
| Scheduler | 1 | 4 | 0 | 4 |
| Collector persistence | 4 | 2 | 2 | 16 |
| Analysis | 2 | 4 | 2 | 12 |
| Notification | 2 | 4 | 2 | 12 |
| Export generation | 2 | 2 | 0 | 4 |
| Migration/maintenance allowance | n/a | n/a | n/a | 10 |
| Total planned maximum |  |  |  | 106 |

The export allocation matches the dedicated worker's initial concurrency of two. Each worker task
uses one short database session at a time; increasing export concurrency or its per-process pool
must increase this budget before deployment.

Production should place PgBouncer in transaction pooling mode between application processes and
PostgreSQL. The table above remains the client-side hard budget; PgBouncer is not permission to
create unbounded pools. Session-level PostgreSQL state, temporary tables, and prepared-statement
assumptions must be reviewed before transaction pooling is enabled.

Operational limits:

- API pool wait p95 target: below 25 ms; alert at 100 ms for five minutes.
- Application connection utilization: alert at 70% of the 120-client budget.
- PostgreSQL total connection utilization: page at 85% of `max_connections`.
- API transaction duration p95 target: below 200 ms; no transaction may include provider I/O.
- Pool timeout: 2 seconds for API reads, 5 seconds for background workers.

Connection-pool size, overflow, timeout, recycle, and statement timeout are explicit application
settings. Production values still require measurement on the target hardware; engine defaults are
not a substitute for an intentional per-process budget.

## Query and pagination contract

All collection-sized API reads use keyset pagination. `OFFSET` pagination is prohibited on
subscriptions, observations, itineraries, alerts, audit events, and deliveries.

- Operational list default/maximum: 50/100 rows.
- History and calendar default/maximum: 200/500 rows; their time/date windows remain mandatory.
- Every page order has a deterministic unique tie-breaker, normally `(timestamp, id)`.
- Mutable history cursors carry an `as_of` UTC snapshot; immutable successful-run offer cursors
  carry the run id and filter fingerprint.
- Date-range endpoints require both lower and upper bounds.
- CSV/JSON exports run as background jobs and read snapshot-fenced keyset pages in short sessions.
- Count totals are omitted from hot endpoints unless served by a maintained aggregate.

Required production access paths:

| Hot path | Keyset order | Target index |
| --- | --- | --- |
| User subscriptions | `created_at DESC, id DESC` | `(user_id, created_at DESC, id DESC)` |
| Fare search offers | `total_price_minor, id` | `(collection_run_id, total_price_minor, id)` with selected payload included |
| Due subscriptions | `next_due_at, id` | partial `(next_due_at, id) WHERE enabled` |
| First-leg route lookup | `departure_date, search_query_id` | partial `(origin_code, destination_code, departure_date, search_query_id) WHERE position = 0` |
| Price history | `observed_at, collection_run_id` | partial partition index `(search_query_id, observed_at, collection_run_id) WHERE is_lowest` with selected payload included |
| Dashboard daily trend | `observation_date` | `(search_query_id, currency, direct_only, observation_date)` with exact daily statistics included |
| Calendar matrix | `departure_date, return_date` | `(search_query_id, departure_date, return_date, observed_at)` |
| Collection-run list | `scheduled_at DESC, id DESC` | `(search_query_id, scheduled_at)` plus bounded top-N merge |
| Pending collection lease | `scheduled_at, id` | partial `(scheduled_at, id) WHERE status = 'pending'` |
| Expired lease recovery | `lease_expires_at, id` | partial `(lease_expires_at, id) WHERE status IN ('leased', 'running')` |

The current route price is selected from the latest finished successful run and applies every local
subscription filter before choosing the cheapest offer. This is required for correctness: the
`latest_price_snapshots` table is keyed only by canonical search/currency/directness and cannot
answer airline, airport, duration, stop, time-window, or target-price filters. The snapshot remains
useful for a future unfiltered market view or cache seed, but it must not silently replace the
filtered route query. Dashboard reads deduplicate identical query/filter pairs and are capped at
100 visible routes.

The initial schema has usable minimum indexes, but several do not yet match the full ordering above.
Those covering/partial indexes must be introduced by a measured migration before the large profile
is accepted. Do not add every proposed `INCLUDE` column blindly; compare index size, write
amplification, buffer hits, and heap fetches first.

## Observation storage and partitions

`price_observations` and `calendar_price_observations` are partitioned monthly by their UTC
observation time. The maintenance job now:

1. Creates current and near-future partitions and keeps the previous month available for late
   writes.
2. Runs under a PostgreSQL transaction advisory lock so multiple schedulers cannot perform the
   same lifecycle action concurrently.
3. Keeps 24 months hot by default, then detaches older partitions and moves the complete tables to
   `farescope_archive` without deleting their observations.
4. Limits each execution to two lifecycle actions by default so maintenance remains bounded.
5. Leaves permanent purge disabled; an operator must configure a purge horizon longer than the
   archive horizon, and only already-archived tables are eligible.

The hot/archive horizon is implemented and PostgreSQL-tested. Normal history APIs still do not read
archived tables, but background CSV/JSON price exports read retained archived
`price_observations` directly under the partition shared lock and a frozen successful-run manifest.
Restore or explicit operational querying is still required to expose archived rows through hot APIs
or rebuild aggregates. Database backups must include both `public` and `farescope_archive`.

Retention that is still `needs scale verification`:

- Redacted collection artifacts: 30 days by default, configurable by administrators.
- Audit and notification delivery metadata: 13 months, excluding encrypted secrets.
- Daily trend rows are maintained for every newly collected UTC day and survive observation
  partition archival. Run the desired historical backfill before detaching a source partition;
  ordinary maintenance cannot reconstruct a day once its raw partition leaves `public`.
- Aggregate retention currently follows its canonical search through `ON DELETE CASCADE`. A
  separate long-term aggregate purge horizon remains `needs scale verification`; do not shorten it
  below the product's visible trend/export horizon.

Partition retention values are bounded deployment settings. Lifecycle work runs in the dedicated
maintenance task and never inside an API request. `DETACH PARTITION` still takes PostgreSQL locks;
the statement timeout, action cap, and operational monitoring remain important on large tables.

Autovacuum and statistics settings are not fixed globally yet. After the large profile, tune the
high-write observation partitions independently and raise statistics targets only for columns whose
selectivity estimates are demonstrably wrong.

## Cache boundary

PostgreSQL remains the source of truth. Redis is permitted for:

- atomic provider/route concurrency leases, start pacing, and owner-fenced heartbeat state;
- short-lived latest-price and route-summary responses;
- SSE fan-out and transient progress state;
- rate-limit counters and idempotency coordination;
- completed immutable report metadata.

Redis must not become the only copy of subscriptions, ownership, alert rules, observations, or
delivery status. Authorization is checked from a signed session plus database ownership, never from
a long-lived object cache.

Latest-price cache entries carry `as_of` and `collection_run_id`, use event-driven invalidation after
a successful collection, and have a 15-60 second safety TTL. Historical windows that can no longer
change may use a longer TTL. A cache miss must degrade to a bounded indexed query rather than a full
history scan.

## SLO targets

These are acceptance targets and remain `needs scale verification` until the reference workload has
been run on production-like hardware.

| Signal | Target |
| --- | --- |
| API availability | 99.9% monthly, excluding announced maintenance |
| Cached/latest read latency | p95 <= 200 ms, p99 <= 750 ms |
| Historical page latency | p95 <= 350 ms, p99 <= 1 s |
| Subscription write latency | p95 <= 500 ms |
| Hot PostgreSQL statement time | p95 <= 75 ms at the large profile |
| Scheduler due-to-lease delay | p95 <= 60 s, p99 <= 5 min |
| Worker lease query | p95 <= 50 ms for a 100-row batch |
| API 5xx rate | below 0.5% over 15 minutes |
| Freshness | 95% of enabled subscriptions collected within one configured interval plus 60 s |

The API latency budget includes application and database time but not the asynchronous upstream
collection duration. Every fare response exposes freshness so a fast response cannot hide stale
data.

## Scale profiles

The workload generator separates users, subscriptions, canonical searches, and observations so the
deduplication benefit is visible.

| Profile | Users | Subscriptions/user | Canonical searches | Observations/search | Approx. observations |
| --- | ---: | ---: | ---: | ---: | ---: |
| Smoke | 25 | 4 | 50 | 24 | 1,200 |
| Reference | 500 | 12 | 2,000 | 240 | 480,000 |
| Large | 5,000 | 20 | 20,000 | 720 | 14,400,000 |

The reference profile is the minimum before merging an index migration. The large profile is the
minimum before claiming multi-user production readiness. Both require saved `EXPLAIN (ANALYZE,
BUFFERS)` output, database size, PostgreSQL settings, machine specification, and three repeated warm
runs.

## Expansion triggers

Scale one bottleneck at a time and preserve the modular-monolith domain boundary.

- Add API processes when API CPU exceeds 65% for 15 minutes while database and pool wait remain
  healthy.
- Reduce pool size or add PgBouncer capacity before adding processes if connection utilization is
  the constraint.
- Add collector processes only when due-to-lease delay grows and provider rate/error limits remain
  healthy.
- Add a read replica for history/reporting only after read load is the measured PostgreSQL
  bottleneck; latest price, ownership, and scheduler reads remain on the primary.
- Promote daily/hourly aggregates when raw-history query cost breaches the SLO despite correct
  pruning and indexes.
- Consider sharding only after a single PostgreSQL primary is demonstrably exhausted and archival,
  partitioning, indexes, pooling, and read replicas are already insufficient.

Trigger an engineering review when any of these lasts for three consecutive 15-minute windows:

- PostgreSQL CPU above 65% or storage latency above 10 ms p95.
- API pool wait above 100 ms p95.
- A hot query p95 exceeds twice its budget.
- Scheduler lag exceeds one collection interval.
- Cache hit ratio for latest-price keys falls below 70% while request volume is high.
- A monthly observation partition is projected to exceed available disk headroom within 45 days.

## Verification workflow

Run the workload and plan suite described in `server/performance/README.md`. A performance claim is
accepted only when:

- all hot queries are bounded and use keyset ordering;
- large tables avoid sequential scans for selective requests;
- sorts stay in memory at the documented page/batch size;
- monthly partition pruning is visible for bounded history queries;
- buffer reads and execution time are stable across three warm runs;
- the result file records commit, schema revision, PostgreSQL version, settings, row counts, and
  machine specification.

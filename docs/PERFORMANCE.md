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
- `needs scale verification`: latency SLOs, connection budgets, index-only behavior, cache hit
  ratios, autovacuum settings, and the large profile query plans.
- `history dependent`: long-range trend aggregation and retention-cost estimates.
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

## Runtime isolation

FareScope remains a modular monolith, but each workload runs as a separate process type:

| Process | Responsibilities | Must not do |
| --- | --- | --- |
| API | Authentication, ownership checks, bounded reads/writes, SSE fan-out | Launch browsers, run large exports, perform long analytics |
| Scheduler | Find due canonical searches, create idempotent jobs, recover expired leases | Hold a database transaction while waiting on Redis or a provider |
| Collector | Playwright/provider access, normalize artifacts, persist one collection result | Share API worker memory or hold a connection while browsing |
| Analysis | Aggregates, trend windows, alert evaluation | Run CPU-heavy work on the API event loop |
| Notification | Deliveries, retries, provider-specific throttles | Recompute price history for every recipient |

Python is appropriate for these I/O-heavy boundaries when the API uses async database access and
CPU-heavy work stays in worker processes or PostgreSQL aggregates. Horizontal API scaling is by
process/container. Browser concurrency is scaled independently and is never coupled to API worker
count.

Every database transaction must finish before any browser request, notification request, or long
Redis wait. A collector may keep normalized data in memory and then open one short persistence
transaction.

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
| Migration/maintenance allowance | n/a | n/a | n/a | 10 |
| Total planned maximum |  |  |  | 102 |

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

Connection-pool values must become explicit settings before production deployment; engine defaults
are not the production configuration.

## Query and pagination contract

All collection-sized API reads use keyset pagination. `OFFSET` pagination is prohibited on
subscriptions, observations, itineraries, alerts, audit events, and deliveries.

- Operational list default/maximum: 50/100 rows.
- History and calendar default/maximum: 200/500 rows; their time/date windows remain mandatory.
- Every page order has a deterministic unique tie-breaker, normally `(timestamp, id)`.
- Mutable history cursors carry an `as_of` UTC snapshot; immutable successful-run offer cursors
  carry the run id and filter fingerprint.
- Date-range endpoints require both lower and upper bounds.
- CSV/JSON exports run as background jobs and stream from a server-side cursor.
- Count totals are omitted from hot endpoints unless served by a maintained aggregate.

Required production access paths:

| Hot path | Keyset order | Target index |
| --- | --- | --- |
| User subscriptions | `created_at DESC, id DESC` | `(user_id, created_at DESC, id DESC)` |
| Fare search offers | `total_price_minor, id` | `(collection_run_id, total_price_minor, id)` with selected payload included |
| Due subscriptions | `next_due_at, id` | partial `(next_due_at, id) WHERE enabled` |
| First-leg route lookup | `departure_date, search_query_id` | partial `(origin_code, destination_code, departure_date, search_query_id) WHERE position = 0` |
| Price history | `observed_at, collection_run_id` | partial partition index `(search_query_id, observed_at, collection_run_id) WHERE is_lowest` with selected payload included |
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

`price_observations` is partitioned monthly by `observed_at` in UTC. The maintenance job must:

1. Create partitions at least three months ahead and keep the previous month available for late
   writes.
2. Check daily that every timestamp in the scheduler horizon maps to a real partition.
3. Run `ANALYZE` after a large backfill and monitor per-partition dead tuples.
4. Detach an expired partition before archive/delete; never perform a massive unbounded delete from
   the parent table.
5. Keep unique constraints and query indexes attached to every child partition.

Initial retention proposal, still `needs scale verification`:

- Normalized raw observations: 13 complete months online.
- Daily price aggregates: 36 months online.
- Redacted collection artifacts: 30 days by default, configurable by administrators.
- Audit and notification delivery metadata: 13 months, excluding encrypted secrets.

Retention values are product settings with system-enforced maximums. Reducing retention queues a
background partition/archive job; it never blocks an API request.

Autovacuum and statistics settings are not fixed globally yet. After the large profile, tune the
high-write observation partitions independently and raise statistics targets only for columns whose
selectivity estimates are demonstrably wrong.

## Cache boundary

PostgreSQL remains the source of truth. Redis is permitted for:

- a distributed collection lock keyed by canonical query hash;
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

# FareScope Living Project Plan

Last updated: 2026-07-21

Status: Core product operational locally; target-server and long-history gates remain.

This is the authoritative product scope, evidence record, architecture decision, implementation
checklist, and progress log. Every material implementation change must update this file in the same
commit. A checkbox means source, persistence, API, UI, and tests are present where applicable; it
does not mean an upstream provider will return every data class for every route on every attempt.

## Status legend

- `verified`: observed in a real response or measured against the running system.
- `implemented`: source, persistence, API, UI, and tests are complete for the stated boundary.
- `needs verification`: code exists or the capability is plausible, but required live evidence is
  still missing.
- `history dependent`: meaningful only after FareScope accumulates enough observations.
- `blocked`: cannot be promised without another authorized source or external state change.

## Product definition

FareScope is a self-hosted airfare data and decision platform, not only a notification script. It
stores normalized observations, exposes current and historical fare data, makes data freshness and
partial-provider responses visible, shares identical collection work across users, and evaluates
owner-scoped alert rules with durable delivery history.

The product focus is airfare data. Account functionality is deliberately minimal.

## Confirmed product requirements

- Server-hosted Web application with no desktop GUI dependency.
- React frontend derived from `/Users/hanhan/Desktop/code/frontend-template/web`.
- One-way and round-trip searches with direct, airline, airport, price, stop, duration, time, cabin,
  and passenger constraints where normalized fields support them.
- Persistent current fares, itinerary details, price history, low-fare calendars, round-trip date
  matrices, collection health, subscriptions, alerts, and delivery attempts.
- Multiple users may register and own independent subscriptions, rules, and notification channels.
- Identical collection queries are canonicalized and collected once for all interested users.
- Advanced recommendations are enabled only after their evidence and sample-size gates are met.

## Minimal user model

- Registration and login require only a username and password.
- Passwords require at least 4 characters and have no composition rule or confirmation field.
- Email is not an account identity and there is no email verification flow.
- There is no invitation, organization, team, member-management, or granular-role product surface.
- The internal admin marker exists only for bootstrap and operational work.
- Public registration can be disabled by deployment configuration.
- Notification destinations are encrypted at rest and never returned in full.
- Every subscription, alert rule, event, and notification channel query is owner-scoped.

## Current upstream evidence

Evidence below was collected on 2026-07-20/21 from the current development network using fresh
browser contexts without a personal profile, stored cookies, or challenge bypass.

| Capability or behavior | Status | Evidence |
| --- | --- | --- |
| Plain HTTP request to the legacy endpoint | blocked | Browser-like Python requests returned HTTP 432 `whaleguard block`. |
| Standard headed Google Chrome | verified | Fresh contexts received HTTP 200 page-generated API responses. |
| Bundled Chromium on the same path | unreliable | It has returned HTTP 432 in the current environment; it remains an explicit fallback only. |
| One-way international calendar | verified | Live capture plus a redacted calendar fixture normalize departure dates and prices. |
| Round-trip calendar/date pairs | verified | Live `SHA-TYO` collection produced 1,130 latest date-pair snapshots per attempt; the redacted fixture covers total-price semantics. |
| Detailed one-way itineraries | verified conditionally | Redacted `batchSearch` fixture covers flights, airports, times, stops, duration, cabin, and total price. |
| Detailed round-trip itineraries | verified conditionally | A live `NKG-BJS` probe normalized 27 itineraries with two ordered legs; a redacted two-leg fixture protects the contract. |
| Detailed `SHA-TYO` results on the latest run | partial | Three bounded attempts returned calendar plus context-only detail responses and zero itineraries. FareScope stored the calendar and reported `partial_fare_data`. |
| Direct-result filtering | implemented locally | Directness is derived from normalized segments and applied to stored offers; a provider-UI direct-only capture comparison is still pending. |
| Baggage/refund rules | needs verification | Stable fields and semantics have not been established. |
| On-time performance | blocked | No authorized operational-flight source is present in captured fare responses. |
| Target server egress | needs verification | Local Chrome success is not evidence that the eventual data-center IP receives the same responses. |

The completed live `SHA-TYO` run used three bounded attempts, stored 3,390 real calendar
observations and 1,130 latest snapshots, ended `success_with_warnings`, released its lease, and
stored zero raw response artifacts. It did not fabricate itinerary offers.

## Feature evidence gates

| Feature | Current state | Gate or limitation |
| --- | --- | --- |
| Current detailed fare table | implemented | Visible when the provider returns normalized detailed offers; otherwise shows a real empty/partial state. |
| Low-fare calendar | implemented | Uses latest date-pair snapshots with keyset pagination. |
| Price history and charts | implemented | Meaning grows as repeated detailed price observations accumulate. |
| Round-trip matrix | implemented | Uses verified departure/return date pairs and total minor-unit prices. |
| Threshold/drop/new-low/direct alerts | implemented | History-based rules require prior detailed observations to trigger meaningfully. |
| Anomaly detection | history dependent | Requires a defined baseline and minimum sample count. |
| Buy-now/wait guidance | history dependent | Requires at least 90 days of suitable horizon data and calibrated confidence. |
| Seasonality analysis | history dependent | Requires at least one meaningful seasonal cycle. |
| Budget destination discovery | needs verification | Requires a bounded route universe and proven collection-rate budget. |
| Multi-provider comparison | blocked for initial release | Requires another authorized adapter and comparable price semantics. |

## Architecture

FareScope is a modular monolith with isolated runtime processes:

- `web`: Vite, React, TypeScript, Tailwind, shadcn/Radix, Lucide, and Recharts.
- `api`: FastAPI async REST API, cookie sessions, owner checks, bounded reads/writes, readiness,
  and request IDs. It never launches browsers.
- `scheduler`: singleton Celery Beat plus short PostgreSQL scheduler transactions.
- `collector`: dedicated headed Google Chrome/Xvfb Celery worker and provider adapters.
- `analysis`: collection-result aggregation and alert evaluation queue.
- `notification`: durable delivery claims, network sends, retry, and audit queue.
- PostgreSQL 16: source of truth, monthly range partitions, latest snapshots, and aggregates.
- Redis: Celery broker/result state; never the only copy of business data.
- PgBouncer: production transaction-pooling boundary.

Provider adapters and browser dependencies are isolated from the API package boundary. Browser or
notification network I/O never occurs inside a database transaction. API and background process
pool sizes, statement timeouts, keyset pagination, and query limits are explicit.

## Collection pipeline

1. A user creates a subscription or submits an on-demand search.
2. The API validates and canonicalizes provider, trip, legs, passengers, cabin, and upstream
   filters into a stable query hash.
3. The scheduler groups due subscriptions by canonical query and creates an idempotent run.
4. A short dispatch lease is committed before the Celery message is published.
5. The collector fences the lease, enters provider/route concurrency and pacing gates, then opens
   a fresh headed Chrome context.
6. Page-generated responses are captured by allowlisted routes; richer repeated responses replace
   context-only envelopes during a bounded settle period.
7. Calendar, itinerary, segment, offer, observation, schema, and latest-snapshot rows are persisted
   transactionally with minor-unit money and UTC timestamps.
8. Retryable network or partial-detail outcomes return to `pending` with bounded exponential
   backoff and jitter. Attempts are capped; calendar-only data remains usable.
9. Analysis evaluates owner rules exactly once per successful run and creates durable events.
10. Notification workers claim deliveries with `SKIP LOCKED`, send outside the transaction, and
    record success or bounded retry state.

The current gate coordinates one collector process. Multiple collector processes or hosts must use
conservative per-process limits until a shared rate-limit coordinator is implemented.

## Implemented data model

- Identity: `users`, `sessions`, `audit_events`.
- Search ownership: `providers`, `search_queries`, `search_legs`, `subscriptions`,
  `subscription_filters`.
- Collection: `collection_runs`, `collection_artifacts`, `schema_observations`.
- Fares: `itineraries`, `segments`, `fare_offers`, partitioned `price_observations`, latest price
  snapshots, partitioned calendar observations, latest calendar snapshots, daily aggregates.
- Alerts: `alert_rules`, rule-channel links, `alert_events`, `notification_channels`, and
  `notification_deliveries`.

Money uses integer minor units plus currency. Timestamps use UTC. Provider-local timestamps retain
their time-zone context. A notification target price is separate from a result maximum-price
filter; a target must never hide the current price it is meant to monitor.

## Implemented Web product

- Username/password registration and login.
- Overview with real subscription counts, route counts, collection success, 30-day trend, and
  recent price change when detailed observations exist.
- Fare explorer with one-way/round-trip forms, direct and local result filters, polling collection
  state, detailed leg cards, and keyset result pagination.
- Subscription creation, pause/resume, delete, independent result/target prices, freshness, and
  latest observed price.
- Price history with raw/hour/day resolution, low-fare calendar, and round-trip matrix.
- Alert rule creation, channel selection, enable/disable, delete, event pagination, and delivery
  audit.
- Webhook, Telegram, Bark, and PushPlus channel creation/toggle with encrypted secrets.
- Collection operations page with health, pagination, attempts, calendar/itinerary/offer counts,
  upstream status, errors, and explicit calendar-only warnings.
- Loading, empty, error, stale, unavailable, and responsive layouts. Demo fare fallback is removed.

The production bundle uses explicit vendor chunks. The main entry fell from about 533 kB to about
182 kB uncompressed in the current build; the largest chunk is about 404 kB and the build emits no
large-chunk warning.

## Performance evidence

The reproducible PostgreSQL 16 reference profile contains 500 users, 6,000 subscriptions with
filters, 2,000 canonical queries, 7,000 runs, 40,000 itineraries/offers, 71,982 segments, 60,000
calendar snapshots, and 480,000 price observations.

Three warm local runs measured:

- filtered search count + page + segments: 0.217-0.630 ms total SQL execution;
- raw history: 0.258-0.452 ms; daily history: 0.259-0.417 ms;
- one-way calendar: 0.080-0.126 ms; round-trip calendar: 0.029-0.036 ms;
- 12-route dashboard latest: 1.465-2.404 ms; trend: 7.965-9.511 ms;
- collection health: 0.263-0.369 ms; run page: about 0.199 ms.

At the hard 100-route dashboard limit, latest and trend application requests measured about 109 ms
and 127 ms. This is within the current local target but scales linearly, so the limit must not be
raised without a snapshot/aggregate redesign. The 14.4-million-observation large profile,
concurrent clients, cold-cache behavior, and production hardware remain unverified. See
`docs/PERFORMANCE.md` for the full contract and reproduction commands.

## Roadmap

### M0: Repository foundation

- [x] Repository, frontend template, instructions, CI code checks, local Compose, and living plan.
- [x] FastAPI, Celery, PostgreSQL, Redis, migrations, production-shaped Compose, and process docs.
- [x] Full local backend/frontend checks and Compose parsing.
- [ ] Migrate Recharts 2 to Recharts 3 with screenshot regression verification.

### M1: Provider collection proof

- [x] Headed Chrome/Xvfb runtime with explicit Chromium fallback and startup smoke checks.
- [x] Redacted one-way, round-trip, and detailed itinerary fixtures with parser contracts.
- [x] Schema diagnostics, optional failure screenshots, partial-data states, and no raw-body default.
- [x] Provider/route concurrency, pacing, jitter, retry backoff, leases, and fencing.
- [x] Scheduler-to-dispatch-to-collector-to-PostgreSQL integration tests.
- [ ] Capture and compare a provider-UI direct-only interaction fixture.
- [ ] Verify repeated live collection from the target server IP.
- [ ] Add shared cross-process/host rate coordination before horizontally scaling collectors.

### M2: Persistence and price APIs

- [x] Canonical query deduplication, monthly partitions, latest snapshots, and migrations through
  `20260720_0010`.
- [x] Calendar, itinerary, segment, offer, raw history, aggregate history, and collection health.
- [x] Owner-scoped keyset pagination and bounded hot queries.
- [x] Independent result filters and notification target prices.
- [x] Reference-scale data generator, service-SQL profiler, and saved performance contract.
- [ ] Automated partition retention/archive jobs and configurable retention controls.
- [ ] Run the large profile with concurrency and cold-cache evidence.

### M3: Minimal multi-user foundation

- [x] Username-only public registration, login, logout, secure cookies, CSRF, and session hashes.
- [x] Four-character minimum password with no confirmation, email, or invitation workflow.
- [x] Per-user subscription, alert, channel, event, and delivery isolation.
- [x] Encrypted notification secrets and authentication/configuration audit events.
- [x] Request IDs and dependency-aware readiness checks.

### M4: Core Web product

- [x] Formal lazy routes and TanStack Query server-state layer.
- [x] Subscription, fare explorer, latest offer, history, calendar, and matrix workflows.
- [x] Alert and notification management plus collection operations/data-quality views.
- [x] Cursor pagination, collection polling, real empty/error/stale states, and vendor chunking.
- [ ] Screenshot-level desktop/mobile visual verification when a browser-control session is
  available.
- [ ] Replace polling with optional SSE for faster collection-state updates.
- [ ] Add saved cross-route comparison views.

### M5: Alerts, reporting, and operations

- [x] Threshold, absolute/percentage drop, new-low, direct, and round-trip range evaluation.
- [x] Durable event/delivery deduplication, cooldown, `SKIP LOCKED` claims, bounded retries, and
  delivery audit.
- [x] PushPlus, Telegram, Bark, and HTTPS Webhook adapters with tests.
- [x] Collection health, run history, retry, upstream status, and partial-data UI.
- [ ] Quiet hours and per-channel delivery schedules.
- [ ] Optional email delivery after an SMTP backend is configured; email remains unrelated to login.
- [ ] Background CSV/JSON exports and retention controls.
- [ ] Schema-drift detail UI, queue-depth metrics, and automated backup/restore drill.

### M6: Evidence-gated intelligence

- [ ] Anomaly detection after minimum sample thresholds are met.
- [ ] Advance-purchase and seasonality analysis after sufficient history exists.
- [ ] Buy-or-wait guidance only with calibrated confidence and transparent evidence.
- [ ] Budget destination discovery after coverage and rate feasibility are proven.
- [ ] Multi-provider comparison only after another authorized adapter exists.

### M7: Production acceptance

- [ ] Verify the actual target host egress and tune collector policy there.
- [ ] Complete large/concurrent/cold-cache performance testing on production-like hardware.
- [ ] Complete restore drill, secret rotation, monitoring, and security review.
- [ ] Build/deploy Docker images when registry/build quota is available. Code and Compose checks do
  not depend on this step.

## Definition of done

A feature is complete only when all applicable items are true:

- Upstream fields and semantics are backed by live evidence or a redacted fixture.
- Normalization and persistence are idempotent or explicitly model repeated real observations.
- API contracts, bounds, ownership, cursors, and failure states are tested.
- UI handles loading, empty, stale, partial, error, and mobile states.
- Queries have appropriate indexes, bounded plans, and representative performance evidence.
- Metrics/logs, request or run traceability, and operational recovery behavior exist.
- Documentation and this progress checklist are updated with the implementation.

## Progress log

### 2026-07-20

- Created the repository, copied the approved frontend template, and selected a process-isolated
  modular monolith.
- Added FastAPI, Celery, PostgreSQL, Redis, migrations, CI code checks, and deployment topology.
- Implemented username/password identity and removed invitations/email login.
- Simplified registration to two fields only and reduced the password minimum to four characters
  without composition rules.
- Added canonical searches, collection persistence, price APIs, Web routes, and redacted Ctrip
  fixtures.
- Verified the initial GitHub code-check workflows; Docker image publication remains disabled.

### 2026-07-21

- Completed collection dispatch, scheduler recovery, Chrome runtime, response settle/richness,
  in-process rate gates, lease fencing, partial-data persistence, and bounded retry integration.
- Ran a real `SHA-TYO` round-trip job end to end: three attempts, 3,390 calendar observations,
  1,130 latest snapshots, no fake detailed offers, no raw artifacts, and a terminal warning state.
- Verified a new user can subscribe to the same canonical query and immediately page through shared
  round-trip calendar data without losing owner isolation.
- Added latest/history/calendar/matrix queries, dashboard aggregates, exact filtered search counts,
  cursor pagination, alert evaluation, non-email delivery adapters, and Web management flows.
- Separated maximum result filtering from notification target prices and migrated legacy-coupled
  rows safely.
- Measured representative PostgreSQL queries on the 480,000-observation reference profile and
  recorded residual 100-route linear dashboard cost.
- Added readiness, request IDs, explicit collection data-quality fields, and vendor bundle chunks.
- Passed full ordinary and PostgreSQL-backed test suites, Ruff, Alembic head/check, frontend lint
  and build, local/production Compose parsing, and live API workflow checks without building Docker
  images.

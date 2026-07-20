# FareScope Living Project Plan

Last updated: 2026-07-20

Status: Foundation

This file is the authoritative product scope, data evidence matrix, architecture record, implementation checklist, and progress log. It must be updated in the same commit as every material implementation change.

## Status legend

- `verified`: observed in a real upstream response and reproducible.
- `needs verification`: plausible, but the exact upstream fields or behavior have not been proven.
- `history dependent`: implementable only after FareScope has accumulated sufficient observations.
- `blocked`: cannot be promised without another authorized data source.
- `implemented`: complete across collection, persistence, API, UI, and tests.

## Product definition

FareScope is a self-hosted airfare data and decision platform. It is not merely a notification utility. The system must preserve detailed observations, expose current itineraries and fare history, explain data freshness, evaluate user-specific alert rules, and remain extensible to additional providers.

## Confirmed requirements

- Server-hosted Web application; no desktop GUI dependency.
- Frontend derived from `/Users/hanhan/Desktop/code/frontend-template/web`.
- One-way and round-trip searches.
- Direct-flight filtering plus airline, airport, time, duration, stop, cabin, and passenger filters when supported by verified fields.
- Multiple routes, multiple travel dates, flexible-date searches, and city or airport codes.
- Current prices, detailed itinerary results, historical price trends, and collection health.
- Persistent subscriptions, alerts, notification delivery history, retries, and deduplication.
- Multiple users in the future, each owning subscriptions and notification channels.
- Advanced analytics are allowed only after their data prerequisites are verified.

## User model decision

FareScope will retain a minimal multi-user foundation from the beginning:

- Users authenticate and own subscriptions, alert rules, saved views, and notification channels.
- Collection queries are canonicalized and deduplicated across users so identical searches do not multiply upstream traffic.
- Initial roles are only `admin` and `member`; enterprise organizations, teams, and granular RBAC are deferred.
- Initial onboarding is admin-created invitations rather than open public registration.
- Secrets such as notification tokens are encrypted at rest and never returned in full after creation.

This avoids unnecessary enterprise permission work while preventing a future single-user schema migration.

## Empirical upstream evidence

The following observations were made on 2026-07-20 from the current development network:

| Capability or behavior | Status | Evidence |
|---|---|---|
| Legacy `lowestPrice` domestic calendar | verified | Headed Chrome returned a populated `oneWayPrice` list for `SHA-BJS`. |
| Legacy `lowestPrice` international calendar | verified unavailable | `SHA-TYO`, `PVG-NRT`, and `PVG-KIX` returned `status: 0` with `oneWayPrice: null`. |
| Plain Python HTTP collection | blocked | Requests with system proxy, direct egress, and browser-like headers all returned HTTP 432 `whaleguard block`. |
| Fresh headed Chrome session | verified | A temporary Chrome context returned HTTP 200 without reusing personal Chrome cookies. |
| Headless Chrome | blocked in current test | The same route returned HTTP 432 and emitted no price response. |
| International flexible-date calendar | verified | `FlightIntlAndInlandLowestPriceSearch` returned 366 price entries with departure date and price fields. |
| Detailed international itineraries | verified | `batchSearch` returned flight numbers, airlines, airports, times, transfers, duration, and fare structures. |
| Round-trip date combinations | verified at response level | The calendar endpoint returned departure/return date pairs when the page requested round-trip calendar data. Normalization is not implemented. |
| Direct-only request semantics | needs verification | The observed request contains a `directFlight` field, but the true direct-only page interaction still needs a captured fixture and result comparison. |
| Baggage and refund rules | needs verification | Detailed response analysis has not yet established stable fields and meanings. |
| On-time performance | blocked | No verified source exists in the captured fare responses. |

## Feature feasibility gates

| Feature | Feasibility | Required evidence before implementation claim |
|---|---|---|
| Current route/date lowest price | verified | Redacted calendar fixture and parser contract tests. |
| Detailed flight result table | verified | Redacted `batchSearch` fixture and itinerary normalization tests. |
| Price history and charts | verified after collection begins | Stored observations across time with deduplication and freshness metadata. |
| Round-trip price matrix | needs normalization proof | Fixture covering departure/return pairs and total-price semantics. |
| Direct-flight filter | needs verification | Captured page request with filter enabled and result-level directness checks. |
| Historical-low and percentage-drop alerts | history dependent | Sufficient observations for the selected comparison window. |
| Price anomaly detection | history dependent | Defined baseline window, minimum sample count, and false-positive tests. |
| Buy-now or wait recommendation | history dependent | At least 90 days of route/date-horizon observations and calibrated confidence reporting. |
| Seasonality and advance-purchase analysis | history dependent | At least one meaningful seasonal cycle; never fabricate before that point. |
| Destination discovery under a budget | needs verification | Bounded route universe, rate budget, and successful batch collection strategy. |
| On-time risk scoring | blocked | Additional authorized operational flight data source. |
| Multi-provider comparison | blocked for initial release | At least one additional provider adapter and comparable price semantics. |

## Architecture decision

FareScope is a modular monolith with separate runtime processes:

- `web`: Vite, React, TypeScript, Tailwind, shadcn/Radix, Lucide, and Recharts.
- `api`: FastAPI REST APIs, authentication, query APIs, and Server-Sent Events.
- `scheduler`: scans due canonical searches and dispatches jobs.
- `collector`: headed Chrome under a virtual display, with provider-specific adapters.
- `analysis`: aggregations, derived metrics, and alert evaluation.
- `notification`: channel delivery, retries, cooldowns, and deduplication.
- PostgreSQL: source of truth; observation tables will use time-based partitioning.
- Redis: Celery broker, short-lived cache, rate-limit state, and distributed locks.

Provider collection must remain replaceable. The API process must never launch browsers.
Browser automation dependencies are installed through the server's `collector` extra so API,
analysis, notification, and ordinary CI environments do not carry the browser runtime.

## Collection pipeline

1. A user creates a subscription or runs an on-demand search.
2. The API normalizes its legs, dates, passengers, cabin, and filters into a canonical query hash.
3. The scheduler groups users sharing that hash and creates one collection job.
4. A collector leases the job, opens the provider search page, and captures page-generated responses.
5. Raw payload metadata is recorded; short-lived raw bodies may be compressed in artifact storage after redaction.
6. Normalizers write collection runs, itineraries, segments, offers, and price observations transactionally.
7. Analysis jobs update daily aggregates and evaluate alert rules.
8. Notification jobs persist every delivery attempt before sending.
9. SSE notifies connected Web clients about fresh observations and job state.

## Planned core entities

- `users`, `sessions`, `invitations`, `audit_events`
- `airports`, `cities`, `providers`
- `search_queries`, `search_legs`, `subscriptions`, `subscription_filters`
- `collection_runs`, `collection_artifacts`, `schema_observations`
- `itineraries`, `segments`, `flights`, `fare_offers`
- `price_observations`, `daily_price_aggregates`
- `alert_rules`, `alert_events`
- `notification_channels`, `notification_deliveries`

Money is stored in integer minor units with currency. Timestamps are stored in UTC, while airport-local timestamps retain their IANA time-zone context. Stable itinerary fingerprints are separate from changing fare offers.

## Planned Web information architecture

- Overview: system freshness, active watches, recent lows, collection health, and notable changes.
- Subscriptions: create, clone, pause, tag, filter, and inspect per-user watches.
- Fare explorer: ad-hoc one-way or round-trip search with flexible dates.
- Route detail: latest, history, fare calendar, round-trip matrix, itineraries, and data quality tabs.
- Compare: airports, destinations, airlines, dates, and saved route groups.
- Alerts: rules, trigger timeline, suppressed events, and delivery attempts.
- Data explorer: normalized observations, raw artifact metadata, exports, and retention.
- Operations: collector workers, queue depth, run history, schema drift, failures, and retries.
- Settings: profile, notification channels, collection policy, retention, and system configuration.

The template shell and semantic components remain. Formal routing and a server-state query layer will be introduced when the first business routes are implemented.

## Alert rules planned

- Current price at or below a fixed target.
- Absolute or percentage drop from the last notified baseline.
- New low over 7, 30, 90, or all retained days.
- Direct itinerary becomes available under a threshold.
- Matching airline, airport, departure window, duration, or stop constraint becomes available.
- Round-trip total enters a target range.
- Data becomes stale or a provider schema changes.

Rules include cooldown, quiet hours, severity, deduplication key, retry policy, and enabled notification channels. Failed delivery never advances the delivered baseline.

## Roadmap

### M0: Repository foundation

- [x] Create and connect the `HanBBQovo/FareScope` repository.
- [x] Derive `web/` from the approved frontend template.
- [x] Add the living project plan and repository instructions.
- [x] Add the FastAPI liveness skeleton and Celery application entrypoint.
- [x] Add PostgreSQL and Redis development services.
- [x] Add baseline frontend and backend CI jobs.
- [ ] Review and migrate the template from unsupported Recharts 2 to Recharts 3 without visual regressions.
- [x] Verify local server tests, frontend lint/build, and Compose configuration.

### M1: Data-source proof on the target server

- [ ] Build a collector runtime with headed Chrome and Xvfb.
- [ ] Verify the target server IP can load the international search page repeatedly.
- [ ] Capture and redact stable one-way, round-trip, direct-only, and detailed-itinerary fixtures.
- [ ] Define schema-drift detection and failure screenshots.
- [ ] Establish per-route rate limits, jitter, backoff, and concurrency limits.
- [ ] Decide the fallback if the server egress is blocked: authorized provider or separately deployed collector node.

### M2: Persistence and collection core

- [ ] Add PostgreSQL models, Alembic migrations, and monthly observation partitions.
- [ ] Implement canonical query hashing and cross-user collection deduplication.
- [ ] Implement the Ctrip collector adapter from verified fixtures.
- [ ] Normalize calendar, itinerary, segment, and offer data.
- [ ] Add collection run state, leases, retries, and idempotency.
- [ ] Add retention, redaction, and raw artifact policies.

### M3: Multi-user product foundation

- [ ] Implement admin bootstrap, invitation, login, logout, and secure cookie sessions.
- [ ] Implement `admin` and `member` authorization boundaries.
- [ ] Implement per-user subscriptions and notification channels.
- [ ] Add audit events for authentication, configuration, and destructive actions.

### M4: Core Web product

- [ ] Add formal routes and server-state query management to the frontend.
- [ ] Implement subscription management and fare explorer.
- [ ] Implement route latest-price, itinerary, history, and calendar pages.
- [ ] Implement the round-trip price matrix and route comparison.
- [ ] Add SSE freshness and collection-status updates.
- [ ] Add responsive desktop and mobile verification.

### M5: Alerts, reporting, and operations

- [ ] Implement rule evaluation and durable alert events.
- [ ] Add PushPlus, email, Telegram, Bark, and Webhook adapters as separately testable channels.
- [ ] Add retry, cooldown, quiet hours, and delivery audit.
- [ ] Add CSV/JSON exports, retention controls, and backup documentation.
- [ ] Add collector health, queue, run, schema-drift, and retry views.

### M6: Evidence-gated intelligence

- [ ] Add anomaly detection after minimum sample thresholds are met.
- [ ] Add advance-purchase and seasonality analysis after sufficient history exists.
- [ ] Add buy-or-wait guidance only with calibrated confidence and transparent evidence.
- [ ] Add budget destination discovery after rate and coverage feasibility are proven.
- [ ] Add multi-provider comparison only after another authorized adapter exists.

## Definition of done for a feature

A feature is complete only when all applicable items are satisfied:

- Upstream data fields and semantics are verified with redacted fixtures.
- Normalization and persistence are implemented with idempotency.
- API contracts and authorization boundaries are tested.
- UI includes loading, empty, stale, error, and mobile states.
- Metrics, logs, and collection-run traceability exist.
- Documentation, roadmap checkbox, and progress log are updated.

## Progress log

### 2026-07-20

- Created the new FareScope repository from the empty GitHub remote.
- Copied the approved frontend template without altering its shadcn preset.
- Chose a modular-monolith architecture with isolated runtime processes.
- Chose a minimal multi-user model rather than a single-user schema or enterprise RBAC.
- Recorded empirical Ctrip data capabilities and explicitly gated unsupported advanced features.
- Added initial API, Celery, PostgreSQL, Redis, CI, and project-governance scaffolding.
- Verified Ruff, Pytest, frontend ESLint, frontend production build, Compose parsing, and Git patch formatting.
- Isolated Playwright in a dedicated `collector` dependency extra so non-collector processes remain lightweight.
- Recorded the template's Recharts 2 end-of-support warning as an explicit M0 migration task.
- Pushed commit `3243ab9` to `origin/main`; GitHub CI run `29714688524` completed successfully.

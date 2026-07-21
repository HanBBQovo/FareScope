# FareScope deployment

`compose.production.yaml` is the production-shaped deployment reference. It is
deliberately separate from the local dependency-only `compose.yaml`; the latter
is still the fastest way to run Postgres and Redis during development.

## Process topology

| Service | Responsibility | Default network exposure |
| --- | --- | --- |
| `web` | Unprivileged Nginx SPA shell and same-origin API proxy | loopback `:8080` |
| `api` | FastAPI HTTP process, two Uvicorn workers by default | internal only |
| `worker` | Celery analysis/notification/default queues | backend + egress |
| `export-worker` | Bounded CSV/JSON generation on the shared export volume | backend |
| `scheduler` | Celery Beat due-work dispatcher | backend only |
| `collector` | Dedicated headed Google Chrome/Xvfb Celery worker | backend + egress |
| `migrate` | One-shot Alembic migration before app startup | backend only |
| `pgbouncer` | Transaction-pooling connection budget | backend only |
| `postgres` | Durable relational store | backend only |
| `redis` | Durable broker/result store | backend only |

The scheduler, collector, analysis, and notification tasks are implemented and
covered by PostgreSQL integration tests. Runtime health still depends on the target
host's provider egress and configured notification destinations.

## First deployment

1. Install Docker Engine with Compose v2 on a Linux host with at least 4 GB RAM
   (the browser collector benefits from 8 GB or more).
2. Copy `deploy/production.env.example` to a deployment-only file, replace every
   placeholder, and use URL-encoded credentials in both database URLs.
3. Put TLS termination and the public hostname in front of the loopback-bound
   Nginx port. The application itself does not terminate certificates.
4. Validate interpolation without starting containers:

   ```bash
   docker compose --env-file deploy/production.env \
     -f compose.production.yaml config --quiet
   ```

5. Build locally and start the stack. GitHub Actions intentionally does not
   build or publish these images until registry quota is available:

   ```bash
   docker compose --env-file deploy/production.env \
     -f compose.production.yaml build api worker scheduler collector web pgbouncer
   docker compose --env-file deploy/production.env \
     -f compose.production.yaml up -d
   docker compose --env-file deploy/production.env \
     -f compose.production.yaml ps
   ```

`migrate` runs before `api`, `worker`, `scheduler`, and `collector`. Re-running
the deployment is idempotent for Alembic migrations; do not run multiple migrate
containers concurrently.

## Connection and resource budget

The application URLs point at PgBouncer, while the migration URL points directly
at PostgreSQL. PgBouncer uses transaction pooling and allows up to 60 server
connections for the application database, with a 40-connection default pool and
10 reserved connections. PostgreSQL starts with 120 total connections, leaving
headroom for migrations, monitoring, and an emergency administrator connection.

`prepared_statement_cache_size=0` in the example URL is conservative for
asyncpg/transaction pooling. If the SQLAlchemy connection factory explicitly
configures PgBouncer-compatible prepared statement names, it can be raised after
load testing. Do not increase API worker or Celery concurrency without measuring
pool wait time, query latency, and Postgres memory.

The default PostgreSQL settings are a safe 4–8 GB host baseline, not universal
tuning. Adjust `POSTGRES_SHARED_BUFFERS`, `POSTGRES_EFFECTIVE_CACHE_SIZE`, and
the process limits in the env file after observing real host memory and query
plans. `statement_timeout`, `lock_timeout`, and
`idle_in_transaction_session_timeout` bound pathological requests instead of
letting one query consume every worker indefinitely.

## Collector operations

The collector image is intentionally separate from the API image and installs
system Google Chrome, Playwright Chromium as an explicit fallback, and Xvfb. It runs one browser task at a time, caps child
lifetimes, provides 1 GiB shared memory, and stores only the configured artifact
directory in a named volume. Keep screenshots and response captures redacted and
apply a retention policy outside Git. Never mount a personal browser profile or
cookies into the container.

The collector requires outbound access to the provider and can therefore be
blocked by a data-center egress policy even when the web/API stack is healthy.
Treat provider response success, schema drift, and anti-bot failures as separate
operational signals.

Production collectors coordinate provider concurrency, route concurrency, and
minimum start intervals through Redis. Every process and host that shares a
provider traffic budget must use the same Redis database, coordination key
prefix, and limit values. Redis/Lua failure is deliberately fail-closed: the
collector cancels the active browser work and returns the database run to its
bounded retry schedule rather than falling back to independent process-local
limits. See `deploy/collector/CHROME_RUNTIME.md` for the exact controls and ACL
requirements.

## Realtime collection status

The authenticated `GET /api/realtime/collection-runs` endpoint uses a bounded
Redis Stream as a cross-process hint log and PostgreSQL as the source of truth.
The API reads the current Stream tail before loading the initial PostgreSQL
snapshot, then starts `XREAD` from that cursor; events committed during the
snapshot query are therefore read after it. Reconnects resume from the browser's
`Last-Event-ID` (or the equivalent `cursor` query parameter).

Every Stream record is resolved by run ID through a new short PostgreSQL session
that checks the live login session and the user's current subscription to the
run's canonical query. The blocking Redis read never holds a database
connection. Session revocation is checked before the snapshot, once per nonempty
Stream batch, on idle heartbeats, and again with every run lookup. Removing the
subscription therefore also removes access to later shared-run events.

The Stream is intentionally scoped to durable subscriptions. An unsaved search
in Fare Explorer has no durable owner-to-query relationship, so it uses bounded
10-second HTTP polling instead of claiming SSE coverage. Saving the route as a
subscription makes later shared collection runs eligible for the Stream.

After every Redis batch has passed its PostgreSQL session/owner checks, the API
emits a data-free `collection-checkpoint` carrying only the acknowledged Stream
cursor. This advances sparse subscribers past global events they cannot see and
prevents those records from being re-read on every connection rotation. Snapshot
cursors and checkpoint cursors are both validated by the browser client; neither
contains query IDs or provider data.

The production defaults retain approximately 20,000 safe state hints, block in
Redis for 15 seconds per read, and rotate each SSE connection after five minutes:

```dotenv
FARESCOPE_COLLECTION_REALTIME_STREAM_KEY=farescope:realtime:collection-runs
FARESCOPE_COLLECTION_REALTIME_STREAM_MAX_LENGTH=20000
FARESCOPE_COLLECTION_REALTIME_BLOCK_MS=15000
FARESCOPE_COLLECTION_REALTIME_CONNECTION_SECONDS=300
FARESCOPE_COLLECTION_REALTIME_RETRY_MS=2000
```

Redis failures emit a redacted `realtime-degraded` event and close the stream.
The web client reconnects with bounded exponential backoff while retaining
low-frequency HTTP polling. A missing or trimmed Redis hint cannot change the
stored task state and is reconciled by the next PostgreSQL snapshot or poll.

The same-origin Nginx layer honors `X-Accel-Buffering: no`. Any additional edge
proxy must also disable response buffering for this endpoint and use a read
timeout longer than the 15-second heartbeat interval, for example:

```nginx
location = /api/realtime/collection-runs {
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 60s;
    proxy_pass http://farescope_api;
}
```

Budget one long-lived Redis connection per active browser SSE connection. The
frontend uses one module-level EventSource per browser page tree and closes it,
including pending reconnect timers, when the last consumer unmounts.

## Observation retention

The hourly partition-maintenance task creates current and near-future price
partitions and applies a bounded two-stage lifecycle under a PostgreSQL advisory
lock. The production defaults are:

```dotenv
FARESCOPE_COLLECTION_PARTITION_ARCHIVE_AFTER_MONTHS=24
FARESCOPE_COLLECTION_PARTITION_PURGE_AFTER_MONTHS=
FARESCOPE_COLLECTION_PARTITION_MAX_ACTIONS=2
```

Archiving detaches an old monthly partition from its hot parent and moves the
table into `farescope_archive`; it does not delete the observations. Archived
tables remain outside the interactive history API, but an owner-scoped background
export can merge them with hot observations without restoring the partitions.
Permanent purge is disabled by default. Enabling it requires a purge horizon
longer than the archive horizon, and the task only drops tables that have already
completed the archive stage. Keep purge disabled until database backups and a
restore drill are proven.

## Background history exports

`POST /api/exports` creates a durable CSV or JSON job for one of the caller's
subscriptions and returns without running the history query. The `exports` Celery
queue claims work with a short transaction, pages observations using
`(observed_at, id)` keyset cursors, closes each database session before writing
the page, and atomically renames a lease-specific temporary file. Every job
persists a creation-time `snapshot_at` for display. In the same transaction it
materializes the IDs of succeeded collection runs visible to PostgreSQL at that
moment; every hot and archive page joins that manifest, so a collection that was
uncommitted or began after job creation cannot appear between pages. API, worker,
and the `farescope-production-export-files` named volume must be deployed
together.

Authorization is resolved through the caller's subscription to its stored
canonical search query. The export contains the full normalized observation set
for that query and UTC half-open range; subscription-side airline and price
display filters do not narrow it. Output includes stable identifiers, timestamps,
currency, integer minor-unit prices, and normalized direct/lowest flags. It does
not include provider raw payloads, browser captures, cookies, or notification
secrets. CSV cells beginning with spreadsheet formula prefixes are escaped.

Archive table identifiers are discovered from PostgreSQL catalogs and accepted
only when they match `price_observations_yYYYYmMM`; requested months further
prune the allowlist. Hot data wins duplicate `(observed_at, id)` rows and final
output is sorted by those fields. Catalog discovery and each page query share a
transaction-scoped partition lock with lifecycle maintenance. Opt-in PURGE skips
months overlapping any pending/running export; no database connection or lock is
held while a page is written. If `farescope_archive` is absent, the same job
continues with hot data only.

The manifest uses the export's UTC half-open range against `finished_at`, which
is the same timestamp persisted as each successful run's observation time; it
does not infer commit visibility from wall-clock `created_at`. The partial
`(search_query_id, finished_at, id)` success index supports that scan. At the
public minimum 30-minute collection interval, the default one-year range is
bounded at about 17,520 IDs per canonical query. Manifests exist only while a job is pending,
running, or retrying; successful and permanently failed jobs delete them in the
terminal transaction, and job deletion has a cascading foreign-key fallback.

Default resource bounds are one year, 250,000 rows, 128 MiB, five active jobs per
owner, 100 active jobs globally, at most 20,000 collection-run IDs per job,
20 retained files/1 GiB per owner, three attempts, and a seven-day file TTL.
The global active limit is serialized by a PostgreSQL advisory transaction lock,
so public registration cannot bypass capacity by spreading work across accounts.
Pending jobs do not reserve shared disk indefinitely. A worker claim writes
the maximum file reservation and uses a short global PostgreSQL advisory lock to
atomically compare all running reservations with the shared volume's absolute
and percentage free-space watermarks. Storage pressure clears that reservation,
remains visible as a pending job with a delayed retry, and does not consume an
attempt. Celery Beat recovers expired leases, retries bounded
failures, finishes durable two-stage deletion, expires job metadata, and removes
only strict, old, unreferenced artifact names in bounded batches. Tune the
`FARESCOPE_EXPORT_*` values in `deploy/production.env`; retained-file count must
not be lower than the active-job limit. Keep the directory at the compose-mounted
`/var/lib/farescope/exports`. Export files are disposable delivery artifacts
rather than backups, while PostgreSQL remains the source of truth.

The production stack gives `exports` its own Celery worker and concurrency budget
(`FARESCOPE_EXPORT_WORKER_CONCURRENCY`, default 2), so large history reads cannot
consume notification or analysis worker slots. A database dispatch lease prevents
Beat and idempotent API replays from publishing the same pending job every tick.
After a successful broker publish, `dispatch_published_at` makes the pending job
ineligible for periodic republish; a worker claim or explicit retry clears it.
Publish failures become eligible after the lease expires. A pending job that is
never claimed fails with `queue_timeout` after 24 hours and releases its manifest.
If Redis loses an already-published message, operators must explicitly clear the
published marker or the owner must delete and recreate the export; maintenance
does not guess by flooding the broker with duplicate messages.

`FARESCOPE_EXPORT_GLOBAL_MAX_ACTIVE_JOBS` and
`FARESCOPE_EXPORT_MANIFEST_MAX_RUNS` are deployment hard limits, defaulting to
100 and 20,000. `FARESCOPE_PUBLIC_REGISTRATION_ENABLED` is explicitly passed to
all server processes and defaults to `true`; disabling registration does not
replace the export resource limits.

The general worker still mounts the same `export-files` volume because the short
`farescope.exports.maintain` task runs on its `default` queue and owns expiry,
two-stage deletion, and orphan cleanup. Missing files are treated idempotently as
already cleaned only inside this shared namespace; never run maintenance against
an empty or different export directory.

## Security and backups

- API, worker, scheduler, collector, web, and PgBouncer run read-only with a
  non-root UID and dropped Linux capabilities. PostgreSQL/Redis use their image
  entrypoints to initialize volumes and then drop to their bundled service user.
- The backend network is marked internal and database ports are not published.
  Only Nginx binds a loopback host port for an external reverse proxy.
- Store the env file in a secret manager or root-readable deployment directory;
  never commit it. Rotate the bootstrap token after first use.
- Back up PostgreSQL with a tested restore procedure. Redis is a broker/cache
  durability aid, not the source of truth. Collector artifacts are diagnostic
  and should have shorter retention than fare observations. Generated exports
  expire automatically and should not be treated as a database backup.
- Include both `public` and `farescope_archive` schemas in PostgreSQL backups;
  archived observations are intentionally preserved outside the hot parents.
- Monitor `docker compose ps`, API liveness, Celery worker heartbeats, queue
  depth, PgBouncer pool wait, PostgreSQL slow/blocked queries, disk usage, and
  collector response-shape failures.

## Known prerequisites and limits

The reference stack uses `/api/health/ready`, which verifies PostgreSQL and Redis,
for the API container healthcheck. `/api/health/live` remains the process-only
liveness endpoint. Celery imports `app.tasks.celery_app:celery_app`.

The compose file is intentionally not a Kubernetes manifest and does not enable
automatic horizontal scaling. Scale API replicas behind the reverse proxy only
after setting an explicit per-instance pool budget; keep the scheduler singleton.
Additional collectors require the same Redis coordination namespace and provider
limits, and should only be added after target-host egress and provider behavior
have been measured.

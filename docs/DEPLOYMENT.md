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
tables are currently outside normal history API queries and must be restored or
queried operationally when old data is needed. Permanent purge is disabled by
default. Enabling it requires a purge horizon longer than the archive horizon,
and the task only drops tables that have already completed the archive stage.
Keep purge disabled until database backups and a restore drill are proven.

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
  and should have shorter retention than fare observations.
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

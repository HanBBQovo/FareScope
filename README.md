# FareScope

FareScope is a self-hosted airfare data and decision platform. It collects page-generated fare
calendars and itinerary quotes, stores normalized history in PostgreSQL, exposes current and
historical views, and evaluates per-user subscription alerts with durable delivery records.

This is a new server-side Web implementation. It does not reuse the legacy `flightAlert` code.

## Current status

The core product runs locally end to end:

- username/password registration with no email or invitation flow;
- owner-scoped subscriptions and notification channels with shared canonical collection;
- one-way/round-trip exploration, direct and local result filters, current detailed offers when the
  provider returns them, history, low-fare calendars, and round-trip matrices;
- headed Google Chrome collection with database leases, Redis cross-process/host pacing, owner
  fencing, retries, partial-data handling, and schema diagnostics;
- alert rules, events, Webhook/Telegram/Bark/PushPlus delivery, retry, delivery audit, and
  per-channel quiet-hour/weekday schedules;
- async FastAPI, Celery process isolation, PostgreSQL partitions/snapshots, Redis, and production
  PgBouncer topology;
- collection queue/run/schema operations views, bounded two-stage partition archive maintenance,
  keyset pagination, readiness, request IDs, and reproducible PostgreSQL concurrency workloads.

The latest live `SHA-TYO` round-trip run stored 1,130 latest calendar snapshots. Ctrip returned no
detailed offers for that sample after three bounded attempts, so FareScope reports a visible
`partial_fare_data` warning instead of fabricating a price. Detailed itinerary contracts are
covered by redacted one-way/round-trip fixtures and have also been observed on another live route.

Reference concurrency is measured and the 100-route dashboard no longer times out at 32 concurrent
API requests. A 1.44-million-observation run still exposed one service-layer pool timeout and raw
trend growth, so the 14.4-million profile, true cold-cache testing, target-server egress, exports,
archived-data access, and history-dependent predictions remain open. The authoritative evidence and
checklist are in [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md).

## Repository layout

```text
FareScope/
|-- web/                       # React + TypeScript Web application
|-- server/                    # FastAPI API, domain modules, workers, tests
|-- server/alembic/            # PostgreSQL migrations
|-- server/performance/        # representative data generator and SQL profiler
|-- deploy/                    # collector, PgBouncer, and Web runtime assets
|-- docs/                      # living plan, deployment, and performance contracts
|-- compose.yaml               # local PostgreSQL and Redis
`-- compose.production.yaml    # production-shaped multi-process topology
```

## Local development

Prerequisites: Python 3.12, `uv`, Node.js 22, Docker Compose, and Google Chrome. Chromium is an
explicit fallback but has been less reliable against the current provider path.

Create local configuration and dependencies:

```bash
cp .env.example .env
docker compose up -d postgres redis
cd server
uv sync --extra dev --extra collector
uv run alembic upgrade head
```

Generate a Fernet key and place it in `.env` as `FARESCOPE_SECRET_ENCRYPTION_KEY` before creating
notification channels:

```bash
cd server
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the API:

```bash
cd server
uv run uvicorn app.main:app --reload --port 8000
```

Run background processes in separate terminals:

```bash
cd server
uv run celery -A app.tasks.celery_app:celery_app worker \
  --queues=default,analysis,notifications --loglevel=INFO

uv run --extra collector celery -A app.tasks.celery_app:celery_app worker \
  --queues=collector --concurrency=1 --loglevel=INFO

uv run celery -A app.tasks.celery_app:celery_app beat --loglevel=INFO
```

For macOS local debugging, add `--pool=solo --concurrency=1` to each worker. Production runs the
collector under Xvfb; see [deploy/collector/CHROME_RUNTIME.md](deploy/collector/CHROME_RUNTIME.md).

Run the frontend and point its proxy at the API port:

```bash
cd web
npm ci
APP_API_PORT=8000 npm run dev
```

Open <http://localhost:5278/> and register with a username plus a password of at least 4 characters.
No email is required. Liveness is `GET /api/health/live`; dependency-aware readiness is
`GET /api/health/ready`.

## Verification

```bash
cd server
uv run ruff check .
uv run pytest -q
FARESCOPE_TEST_DATABASE_URL=postgresql+asyncpg://farescope:farescope@127.0.0.1:5432/farescope \
  uv run pytest -q
FARESCOPE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
  uv run pytest -q tests/collectors/runtime/test_redis_gate_integration.py
uv run alembic check

cd ../web
npm run lint
npm run build

cd ..
docker compose -f compose.yaml config --quiet
docker compose --env-file deploy/production.env.example \
  -f compose.production.yaml config --quiet
```

Live provider tests are opt-in and must use a fresh browser context. They do not mount personal
profiles, reuse cookies, bypass challenges, or save raw response bodies.

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). GitHub Actions currently runs code checks only. Docker
image build and publication are intentionally skipped while build/registry quota is unavailable.

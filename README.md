# FareScope

FareScope is a self-hosted airfare data and decision platform. It collects fare calendars and itinerary quotes, preserves historical observations, exposes route analytics, and evaluates subscription alert rules.

This repository is a new implementation. It does not reuse the legacy `flightAlert` code.

## Current status

The project is in the foundation phase. The API health endpoint, development services, frontend template, CI skeleton, and living implementation plan are present. Provider collection and product pages are not implemented yet.

The authoritative scope, evidence matrix, roadmap, and progress log live in [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md).

## Repository layout

```text
FareScope/
├── web/                 # Vite + React + TypeScript + shadcn frontend
├── server/              # FastAPI modular monolith and worker entrypoints
├── docs/                # Living product and implementation plan
├── compose.yaml         # PostgreSQL and Redis development services
└── .github/workflows/   # Baseline CI
```

## Local development

Start PostgreSQL and Redis:

```bash
cp .env.example .env
docker compose up -d postgres redis
```

Start the API:

```bash
cd server
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000
```

Start the frontend:

```bash
cd web
npm ci
npm run dev
```

The API liveness endpoint is `GET http://localhost:8000/api/health/live`. The frontend development server uses `http://localhost:5278`.

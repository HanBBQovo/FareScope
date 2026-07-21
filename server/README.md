# FareScope Server

The server is a modular Python application with separate process entrypoints for
HTTP APIs, scheduled dispatch, browser collection, analysis, and notification
delivery. PostgreSQL is the source of truth; Redis provides Celery transport,
cross-process collector coordination, and a bounded collection-state event log.

`GET /api/realtime/collection-runs` is an authenticated Server-Sent Events
endpoint. It sends an owner-scoped PostgreSQL snapshot, resumes Redis Stream
records from `Last-Event-ID` or the `cursor` query parameter, and re-reads each
run from PostgreSQL while rechecking the current session and canonical-query
subscription. Stream records contain only safe identifiers, state, timestamps,
and bounded error codes. They never contain provider payloads, browser state,
cookies, or notification secrets.

Collector tasks publish `running`, retry, and terminal state only after the
corresponding database transaction commits. Redis publication is best-effort:
the snapshot and frontend polling fallback reconcile missed hints without
rolling back fare data or task state.

`/api/exports` provides durable, owner-scoped CSV/JSON history export jobs. The
API only validates and records bounded work; the `exports` Celery queue reads the
authorized canonical query from hot and catalog-allowlisted archived partitions,
then writes lease-fenced files outside database transactions. Downloads expose
normalized UTC/minor-unit observations only and expire on the configured TTL.
Worker claims atomically reserve shared-volume capacity across users, every page
is fenced by a creation-visible succeeded-run manifest, and archive PURGE skips
active export ranges. `snapshot_at` remains presentation metadata rather than a
wall-clock substitute for PostgreSQL commit visibility.

# Collector browser runtime

The production collector has two explicit image targets:

| Image target | Browser channel | Intended use |
| --- | --- | --- |
| `collector-runtime` | `chrome` | Production default; launches the system Google Chrome channel. |
| `collector-runtime-chromium` | `chromium` | Explicit fallback when the host architecture or image policy cannot install Google Chrome. |

The default target installs both the Playwright-managed Chromium binary and
Google Chrome. The collector never falls back silently: the selected channel is
checked at startup and recorded as safe run metadata. This matters because the
provider can return different anti-bot responses for bundled Chromium and
Google Chrome.

## Production default

Use the values in `deploy/production.env` (copied from the example):

```dotenv
FARESCOPE_COLLECTOR_IMAGE_TARGET=collector-runtime
FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chrome
```

The image build runs `playwright install --with-deps chrome` and verifies that
the `google-chrome` executable responds to `--version`. At container startup,
the entrypoint performs a metadata-only executable check and a headed
`about:blank` browser smoke test under Xvfb before starting the Celery worker.
The smoke test does not open a provider page, use cookies, mount a profile, or
write response payloads.

## Explicit Chromium fallback

If Google Chrome is unavailable for a documented operational reason, set both
the image target and channel together:

```dotenv
FARESCOPE_COLLECTOR_IMAGE_TARGET=collector-runtime-chromium
FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chromium
```

Do not select `chrome` with the Chromium-only target. The startup doctor is
intentionally fail-closed and will keep the collector unhealthy instead of
silently changing browser identity.

The standard Chrome target currently assumes a Linux architecture supported by
Google's Chrome package (normally `amd64`). On another architecture, use the
explicit Chromium fallback only after validating the provider response and
normalization pipeline for that host.

## Verification and health

The collector healthcheck combines three signals:

1. The selected browser executable is present.
2. The startup smoke marker proves a headed browser launched under Xvfb.
3. Celery responds to a worker ping on the collector queue.

For a manual, metadata-only check inside a running container:

```bash
docker compose --env-file deploy/production.env \
  -f compose.production.yaml exec collector \
  python /app/scripts/collector/runtime_smoke.py doctor \
  --browser-channel "$FARESCOPE_COLLECTOR_BROWSER_CHANNEL" \
  --skip-display-check
```

To verify headed launch without provider traffic, run the browser smoke command
under the same Xvfb wrapper used by the image:

```bash
docker compose --env-file deploy/production.env \
  -f compose.production.yaml exec collector \
  xvfb-run -a --server-args="-screen 0 1440x900x24 -nolisten tcp" \
  python /app/scripts/collector/runtime_smoke.py browser-smoke \
  --browser-channel "$FARESCOPE_COLLECTOR_BROWSER_CHANNEL"
```

Provider capture is a separate opt-in check. A successful local smoke test does
not prove that the deployment IP will receive the same response; monitor HTTP
432, response-shape, and normalization diagnostics independently.

## Collection pacing and retry controls

The collector applies pacing around provider traffic before launching a browser
session. Configure these values in `deploy/production.env` when tuning a worker:

```dotenv
FARESCOPE_COLLECTION_COORDINATION_BACKEND=redis
FARESCOPE_COLLECTION_COORDINATION_LEASE_TTL_SECONDS=180
FARESCOPE_COLLECTION_COORDINATION_ACQUIRE_TIMEOUT_SECONDS=120
FARESCOPE_COLLECTION_COORDINATION_POLL_INTERVAL_SECONDS=0.5
FARESCOPE_COLLECTION_COORDINATION_REDIS_TIMEOUT_SECONDS=2
FARESCOPE_COLLECTION_COORDINATION_KEY_PREFIX=farescope:collection-coordination
FARESCOPE_COLLECTION_PROVIDER_CONCURRENCY=2
FARESCOPE_COLLECTION_ROUTE_CONCURRENCY=1
FARESCOPE_COLLECTION_MINIMUM_INTERVAL_SECONDS=3
FARESCOPE_COLLECTION_JITTER_SECONDS=1
FARESCOPE_COLLECTION_CAPTURE_SETTLE_SECONDS=2
FARESCOPE_COLLECTION_RETRY_BASE_SECONDS=60
FARESCOPE_COLLECTION_RETRY_MAX_SECONDS=1800
FARESCOPE_COLLECTION_RETRY_JITTER_RATIO=0.2
```

`ROUTE_CONCURRENCY` limits the same normalized search route, while
`PROVIDER_CONCURRENCY` limits all routes for a provider. The interval and
jitter apply to starts of a route slot. In production, one Redis Lua operation
atomically prunes expired owners, checks both concurrency limits, and reserves
the route's next start using Redis server time. Provider and route identifiers
are hashed in Redis keys, and all keys share one cluster hash slot.

Every acquired slot has an unguessable owner token and a Redis TTL. The worker
renews its token while the browser is active; release removes only that exact
token. A crashed worker therefore stops consuming capacity after
`LEASE_TTL_SECONDS`, while a stale or incorrect owner cannot release another
worker's slot. Keep the TTL above the longest browser operation; production
settings reject values below 150 seconds. The database collection-run lease is
also rechecked and renewed after waiting for a Redis slot and before provider
traffic begins.

If a heartbeat detects a Redis error or lost owner while the browser is still
running, it cancels that browser body immediately. The context converts the
cancellation into a retryable coordination failure, so the task does not wait
for a normal browser timeout or accept a result collected without a live slot.
The minimum TTL remains a second safety boundary for abrupt process loss and
event-loop stalls. Configuration also requires `ACQUIRE_TIMEOUT_SECONDS` to be
shorter than the database collection-run lease.

Redis coordination is fail-closed. A connection error, acquire timeout, failed
renewal, or fenced release prevents a successful collection result and records
a retryable `collection_coordination_*` failure through the bounded durable
retry schedule. There is no automatic fallback to process-local limits because
that would multiply provider traffic during a Redis incident. The `local`
backend remains available only for development and isolated tests; production
configuration requires `redis`.

All collector processes and hosts sharing a provider must use the same Redis
database, key prefix, and limit values. Use a different key prefix to isolate a
separate environment that intentionally has its own traffic budget. An external
Redis ACL must permit `EVAL` and the scripts' `TIME`, string, expiry, and sorted
set commands; a service that disables Lua will fail closed and surface the same
retryable coordination error.

The browser waits for the first required response set and then keeps the page
open for `CAPTURE_SETTLE_SECONDS` so delayed `/search/pull/` responses can
replace an empty envelope with a richer detailed response. A calendar-only run
is persisted as partial data and returned to the durable retry schedule. Retry
backoff is bounded by `RETRY_MAX_SECONDS` and receives the configured bounded
jitter, so simultaneous failures do not all retry at one instant.

Never mount a personal Chrome profile, cookie store, or browser cache into the
collector. Keep optional failure screenshots outside the repository and give
them a short retention period.

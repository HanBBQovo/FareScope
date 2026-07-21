# Browser collection runtime

This package observes JSON requests made by a normal browser page. It does
not recreate Ctrip request signatures, reuse a personal browser profile, store
cookies, patch browser fingerprints, or claim guaranteed access to provider controls.

## Runtime guarantees

- Standard Chrome is launched with `headless=false`, the mode verified against the provider.
- macOS uses `open -gj` plus a new temporary profile and CDP connection, keeping the window hidden.
- Linux production runs headed Chrome inside Xvfb, so no desktop window is visible.
- Every run creates isolated, non-persistent browser state. No personal profile, storage state,
  cookies, custom user agent, request headers, or response headers are loaded from disk.
- The macOS process is terminated by its unique temporary profile after every run, including
  failures. The profile directory is then removed.
- A bounded CDP buffer retains large matched responses, including round-trip search results, only
  until the current run has normalized them.
- Matched JSON remains in process memory. Diagnostics retain the URL origin/path but
  remove query strings, fragments, and URL credentials.
- HTTP 432, timeout, missing response schema, browser availability, navigation, and
  response decoding failures have stable labels.
- Provider and route concurrency, start intervals, jitter, retries, and exponential
  backoff are bounded by explicit policies.

## Background execution

On macOS, the verified default hides the headed Chrome instance:

```bash
uv run --extra collector python scripts/collector/runtime_smoke.py browser-smoke \
  --browser-channel chrome --headed --background
```

On Linux, set `DISPLAY` or launch the worker/CLI under Xvfb:

```bash
xvfb-run -a --server-args="-screen 0 1440x900x24" \
  uv run --extra collector python scripts/collector/runtime_smoke.py browser-smoke --headed
```

Install the matching Chromium runtime during image/host provisioning (not on every
job):

```bash
uv run --extra collector playwright install --with-deps chromium
```

Run a metadata-only capture check for a page URL:

```bash
xvfb-run -a uv run --extra collector python scripts/collector/runtime_smoke.py capture \
  --page-url 'https://flights.ctrip.com/online/list/oneway-sha-tyo?depdate=2026-08-15' \
  --expect calendar --expect batch_search --browser-channel chrome --headed
```

The command prints capture names, statuses, safe URLs, and top-level JSON keys. It
does not print or save payload values. A screenshot is written only on failure and
only when `--screenshot-directory` is provided.

## Evidence boundary

A fresh, hidden standard Chrome session returned the international search page plus page-generated
`batchSearch` and lowest-price responses on the development Mac. A live one-way `SHA-TYO` run
persisted 141 itineraries and 469 offers; a direct round-trip run persisted 49 itineraries and
1,303 offers. True headless Chrome and bundled Chromium have returned HTTP 432 on the same path.
Operation from a Linux server and its egress IP remains **needs verification**; failures remain
visible rather than producing fabricated prices.

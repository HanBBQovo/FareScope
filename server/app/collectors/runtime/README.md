# Browser collection runtime

This package observes JSON requests made by a normal, headed Chromium page. It does
not recreate Ctrip request signatures, reuse a personal browser profile, install
stealth patches, or claim to bypass provider controls.

## Runtime guarantees

- Chromium is always launched with `headless=False`.
- Every run creates a new non-persistent browser context. No storage state, cookies,
  profile directory, request headers, or response headers are loaded from disk.
- Matched JSON remains in process memory. Diagnostics retain the URL origin/path but
  remove query strings, fragments, and URL credentials.
- HTTP 432, timeout, missing response schema, browser availability, navigation, and
  response decoding failures have stable labels.
- Provider and route concurrency, start intervals, jitter, retries, and exponential
  backoff are bounded by explicit policies.

## Linux and Xvfb

Headed Chromium needs a display. In a Linux worker, set `DISPLAY` yourself or launch
the worker/CLI under Xvfb:

```bash
xvfb-run -a --server-args="-screen 0 1440x900x24" \
  uv run --extra collector python scripts/collector/runtime_smoke.py browser-smoke
```

Install the matching Chromium runtime during image/host provisioning (not on every
job):

```bash
uv run --extra collector playwright install --with-deps chromium
```

Run a metadata-only capture check for a page URL:

```bash
xvfb-run -a uv run --extra collector \
  python scripts/collector/runtime_smoke.py capture \
  --page-url 'https://flights.ctrip.com/online/list/oneway-sha-tyo?depdate=2026-08-15' \
  --expect calendar --expect batch_search
```

The command prints capture names, statuses, safe URLs, and top-level JSON keys. It
does not print or save payload values. A screenshot is written only on failure and
only when `--screenshot-directory` is provided.

## Evidence boundary

A fresh headed Chrome session has previously returned the international search page
and page-generated responses on the development Mac. Operation from a Linux server,
its display stack, and its egress IP remains **needs verification**. HTTP 432 remains
an observable upstream anti-bot outcome; this runtime does not evade it.

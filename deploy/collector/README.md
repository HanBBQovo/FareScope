# Collector host prerequisites

FareScope's default collector uses standard Google Chrome in a headed Xvfb display. The production
collector image also contains Playwright Chromium as an explicit fallback, but the bundled browser
has received different anti-bot responses in the current network and must not be assumed
equivalent.

For local Chrome verification from the `server` directory:

```bash
uv sync --extra collector --extra dev
FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chrome \
  uv run --extra collector python scripts/collector/runtime_smoke.py browser-smoke
```

On a Linux host, use the production entrypoint/Xvfb setup documented in
[CHROME_RUNTIME.md](CHROME_RUNTIME.md). If Google Chrome cannot be installed, provision the
Playwright browser and explicitly select the fallback only after a live response check:

```bash
uv run --extra collector playwright install --with-deps chromium
FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chromium \
  xvfb-run -a --server-args="-screen 0 1440x900x24" \
  uv run --extra collector python scripts/collector/runtime_smoke.py browser-smoke
```

Never mount a personal Chrome profile or cookie directory. Failure screenshots stay outside the
repository with short retention; raw response bodies are not stored by default.

The current target-server egress path is not yet verified. A successful local headed run is not
evidence that a data-center IP will receive the same provider response.

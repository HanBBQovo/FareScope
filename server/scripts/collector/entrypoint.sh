#!/bin/sh
set -eu

umask 077

browser_channel="${FARESCOPE_COLLECTOR_BROWSER_CHANNEL:-chrome}"
case "$browser_channel" in
    chrome|chromium) ;;
    *)
        echo "Unsupported collector browser channel: $browser_channel" >&2
        exit 2
        ;;
esac

export FARESCOPE_COLLECTOR_BROWSER_CHANNEL="$browser_channel"
mkdir -p "$HOME"
rm -f "$HOME/browser-ready"

/opt/farescope/bin/python /app/scripts/collector/runtime_smoke.py doctor \
    --browser-channel "$browser_channel" \
    --skip-display-check

exec xvfb-run -a --server-args="-screen 0 1440x900x24 -nolisten tcp" \
    sh -c '
        /opt/farescope/bin/python /app/scripts/collector/runtime_smoke.py browser-smoke \
            --browser-channel "$FARESCOPE_COLLECTOR_BROWSER_CHANNEL"
        touch "$HOME/browser-ready"
        exec "$@"
    ' sh "$@"

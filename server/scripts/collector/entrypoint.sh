#!/bin/sh
set -eu

umask 077

browser_channel="${FARESCOPE_COLLECTOR_BROWSER_CHANNEL:-chrome}"
browser_headless="${FARESCOPE_COLLECTOR_BROWSER_HEADLESS:-false}"
browser_background="${FARESCOPE_COLLECTOR_BROWSER_BACKGROUND:-true}"
case "$browser_channel" in
    chrome|chromium) ;;
    *)
        echo "Unsupported collector browser channel: $browser_channel" >&2
        exit 2
        ;;
esac
case "$browser_headless" in
    1|true|TRUE|yes|YES|on|ON|headless)
        browser_headless=true
        browser_mode_arg=--headless
        ;;
    0|false|FALSE|no|NO|off|OFF|headed)
        browser_headless=false
        browser_mode_arg=--headed
        ;;
    *)
        echo "Unsupported collector browser headless value: $browser_headless" >&2
        exit 2
        ;;
esac

export FARESCOPE_COLLECTOR_BROWSER_CHANNEL="$browser_channel"
export FARESCOPE_COLLECTOR_BROWSER_HEADLESS="$browser_headless"
export FARESCOPE_COLLECTOR_BROWSER_BACKGROUND="$browser_background"
mkdir -p "$HOME"
rm -f "$HOME/browser-ready"

doctor_display_arg="--skip-display-check"

/opt/farescope/bin/python /app/scripts/collector/runtime_smoke.py doctor \
    --browser-channel "$browser_channel" \
    $browser_mode_arg \
    $doctor_display_arg

if [ "$browser_headless" = true ]; then
    /opt/farescope/bin/python /app/scripts/collector/runtime_smoke.py browser-smoke \
        --browser-channel "$FARESCOPE_COLLECTOR_BROWSER_CHANNEL" \
        --headless
    touch "$HOME/browser-ready"
    exec "$@"
fi

exec xvfb-run -a --server-args="-screen 0 1440x900x24 -nolisten tcp" \
    sh -c '
        /opt/farescope/bin/python /app/scripts/collector/runtime_smoke.py browser-smoke \
            --browser-channel "$FARESCOPE_COLLECTOR_BROWSER_CHANNEL" \
            --headed
        touch "$HOME/browser-ready"
        exec "$@"
    ' sh "$@"

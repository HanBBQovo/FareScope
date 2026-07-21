from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings

_SCRIPT = Path(__file__).parents[2] / "scripts" / "collector" / "runtime_smoke.py"
_SCRIPT_NAMESPACE = runpy.run_path(str(_SCRIPT))
build_parser = _SCRIPT_NAMESPACE["build_parser"]
_browser_channel = _SCRIPT_NAMESPACE["_browser_channel"]


def test_capture_cli_accepts_explicit_chrome_channel() -> None:
    args = build_parser().parse_args(
        [
            "capture",
            "--page-url",
            "https://flights.ctrip.com/online/list/oneway-sha-tyo",
            "--expect",
            "calendar",
            "--browser-channel",
            "chrome",
        ]
    )

    assert args.browser_channel == "chrome"
    assert _browser_channel(args.browser_channel) == "chrome"


def test_doctor_cli_accepts_channel_and_display_override() -> None:
    args = build_parser().parse_args(
        ["doctor", "--browser-channel", "chrome", "--skip-display-check"]
    )

    assert args.browser_channel == "chrome"
    assert args.skip_display_check is True


def test_cli_and_settings_keep_bundled_chromium_as_default() -> None:
    args = build_parser().parse_args(["browser-smoke"])

    assert args.browser_channel == "chromium"
    assert _browser_channel(args.browser_channel) is None
    assert Settings().collector_browser_channel is None
    assert Settings(collector_browser_channel="chromium").collector_browser_channel is None


def test_settings_reject_unknown_browser_channel() -> None:
    with pytest.raises(ValidationError, match="collector_browser_channel"):
        Settings(collector_browser_channel="msedge")

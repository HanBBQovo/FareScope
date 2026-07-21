"""Metadata-only doctor, browser smoke, and Ctrip response capture CLI."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.collectors.runtime import (
    BrowserRunConfig,
    PlaywrightCaptureRunner,
    ctrip_capture_rules,
)

_BROWSER_CHANNELS = ("chromium", "chrome")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor",
        help="Check dependency, browser executable, and headed display prerequisites",
    )
    _add_browser_channel_argument(doctor)
    doctor.add_argument(
        "--skip-display-check",
        action="store_true",
        help="Check the browser installation without requiring an active display",
    )
    _add_browser_mode_arguments(doctor)
    browser_smoke = commands.add_parser(
        "browser-smoke",
        help="Launch a browser without provider traffic",
    )
    _add_browser_channel_argument(browser_smoke)
    _add_browser_mode_arguments(browser_smoke)

    capture = commands.add_parser("capture", help="Observe known page-generated responses")
    capture.add_argument("--page-url", required=True)
    capture.add_argument(
        "--expect",
        action="append",
        choices=("calendar", "batch_search"),
        required=True,
    )
    capture.add_argument("--route-key", default="manual-smoke")
    capture.add_argument("--navigation-timeout", type=float, default=60.0)
    capture.add_argument("--capture-timeout", type=float, default=45.0)
    capture.add_argument("--screenshot-directory", type=Path)
    capture.add_argument("--proxy-server")
    _add_browser_channel_argument(capture)
    _add_browser_mode_arguments(capture)
    return parser


def _add_browser_channel_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--browser-channel",
        choices=_BROWSER_CHANNELS,
        default="chrome",
    )


def _add_browser_mode_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=None,
        help="Use Chrome's true headless mode (provider compatibility may differ)",
    )
    group.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Use the standard headed browser kernel; Linux requires DISPLAY or Xvfb",
    )
    background_group = parser.add_mutually_exclusive_group()
    background_group.add_argument(
        "--background",
        dest="background",
        action="store_true",
        default=None,
        help="Hide headed Chrome on macOS; Linux uses Xvfb",
    )
    background_group.add_argument(
        "--foreground",
        dest="background",
        action="store_false",
        help="Allow a headed browser window to be shown for debugging",
    )


def doctor_summary(
    browser_channel: str = "chrome",
    *,
    headless: bool = False,
    background: bool = True,
    require_display: bool = True,
) -> dict[str, Any]:
    is_linux = platform.system() == "Linux"
    playwright_installed = importlib.util.find_spec("playwright") is not None
    browser_found, browser_version = _browser_runtime_status(browser_channel)
    display_configured = not is_linux or bool(os.environ.get("DISPLAY"))
    return {
        "success": (
            playwright_installed
            and browser_found
            and (display_configured or not require_display)
        ),
        "playwright_python_installed": playwright_installed,
        "platform": platform.system(),
        "display_required": require_display and not headless,
        "display_configured": display_configured,
        "headless": headless,
        "headed_mode": not headless,
        "background": background,
        "browser_channel": browser_channel,
        "browser_executable_found": browser_found,
        "browser_version": browser_version,
        "persistent_profile": False,
        "provider_network_requested": False,
    }


async def run_browser_smoke(args: argparse.Namespace) -> dict[str, Any]:
    headless = _headless_mode(args)
    background = _background_mode(args)
    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="local",
            route_key="browser-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            browser_channel=_browser_channel(args.browser_channel),
            headless=headless,
            background=background,
        ),
        capture_rules=(),
    )
    summary = serialize_result(result)
    summary["browser_channel"] = args.browser_channel
    summary["headless"] = headless
    summary["background"] = background
    summary["provider_network_requested"] = False
    return summary


async def run_capture(args: argparse.Namespace) -> dict[str, Any]:
    headless = _headless_mode(args)
    background = _background_mode(args)
    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key=args.route_key,
            page_url=args.page_url,
            expected_capture_names=frozenset(args.expect),
            navigation_timeout_seconds=args.navigation_timeout,
            capture_timeout_seconds=args.capture_timeout,
            screenshot_directory=args.screenshot_directory,
            proxy_server=args.proxy_server,
            browser_channel=_browser_channel(args.browser_channel),
            headless=headless,
            background=background,
        ),
        capture_rules=ctrip_capture_rules(),
    )
    summary = serialize_result(result)
    summary["browser_channel"] = args.browser_channel
    summary["headless"] = headless
    summary["background"] = background
    return summary


def serialize_result(result: Any) -> dict[str, Any]:
    return {
        "success": result.success,
        "provider": result.provider,
        "route_key": result.route_key,
        "captured": [
            {
                "name": capture.capture_name,
                "status_code": capture.status_code,
                "url_without_query": capture.url_without_query,
                "top_level_keys": sorted(str(key) for key in capture.payload),
            }
            for capture in result.captures
        ],
        "missing": sorted(result.missing_capture_names),
        "diagnostics": [
            {
                "kind": diagnostic.kind.value,
                "capture_name": diagnostic.capture_name,
                "status_code": diagnostic.status_code,
                "retryable": diagnostic.retryable,
                "message": diagnostic.message,
                "details": dict(diagnostic.details),
            }
            for diagnostic in result.diagnostics
        ],
        "screenshot_path": str(result.screenshot_path) if result.screenshot_path else None,
        "raw_payload_written": False,
    }


def _browser_channel(value: str) -> str | None:
    return None if value == "chromium" else value


def _headless_mode(args: argparse.Namespace) -> bool:
    if getattr(args, "headless", None) is not None:
        return bool(args.headless)
    return _env_default_headless()


def _env_default_headless() -> bool:
    raw = os.getenv("FARESCOPE_COLLECTOR_BROWSER_HEADLESS")
    if raw is None:
        return False
    return raw.strip().casefold() not in {"0", "false", "no", "off", "headed"}


def _background_mode(args: argparse.Namespace) -> bool:
    if getattr(args, "background", None) is not None:
        return bool(args.background)
    raw = os.getenv("FARESCOPE_COLLECTOR_BROWSER_BACKGROUND")
    if raw is None:
        return True
    return raw.strip().casefold() not in {"0", "false", "no", "off", "foreground"}


def _browser_runtime_status(browser_channel: str) -> tuple[bool, str | None]:
    executable: Path | None = None
    if browser_channel == "chrome":
        executable = _find_system_chrome()
    elif browser_channel == "chromium":
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                executable = Path(playwright.chromium.executable_path)
        except Exception:  # noqa: BLE001 - doctor must turn install errors into status
            executable = None
    else:
        return False, None

    if executable is None or not executable.is_file():
        return False, None
    return True, _browser_version(executable)


def _find_system_chrome() -> Path | None:
    candidates = (
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        "/opt/google/chrome/chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    return None


def _browser_version(executable: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - executable is resolved locally
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0][:200] if output else None


def main() -> int:
    args = build_parser().parse_args()
    command = args.command or "doctor"
    if command == "doctor":
        headless = _headless_mode(args)
        summary = doctor_summary(
            getattr(args, "browser_channel", "chrome"),
            headless=headless,
            background=_background_mode(args),
            require_display=not getattr(args, "skip_display_check", False) and not headless,
        )
    elif command == "browser-smoke":
        summary = asyncio.run(run_browser_smoke(args))
    else:
        summary = asyncio.run(run_capture(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

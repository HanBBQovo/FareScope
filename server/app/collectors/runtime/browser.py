"""Async Playwright runner using headed Chromium and isolated contexts."""

from __future__ import annotations

import asyncio
import os
import platform
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.collectors.runtime.capture import ResponseCapture
from app.collectors.runtime.models import (
    BrowserRunResult,
    CaptureDiagnostic,
    CaptureRule,
    FailureKind,
)


@dataclass(frozen=True, slots=True)
class BrowserRunConfig:
    provider: str
    route_key: str
    page_url: str
    expected_capture_names: frozenset[str]
    navigation_timeout_seconds: float = 60.0
    capture_timeout_seconds: float = 45.0
    post_capture_settle_seconds: float = 0.0
    screenshot_directory: Path | None = None
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    viewport_width: int = 1440
    viewport_height: int = 900
    wait_until: str = "domcontentloaded"
    require_display_on_linux: bool = True
    proxy_server: str | None = None
    browser_channel: str | None = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.page_url)
        is_about_blank = parsed.scheme == "about" and self.page_url == "about:blank"
        if not self.provider.strip() or not self.route_key.strip():
            raise ValueError("provider and route_key must not be empty")
        if parsed.scheme not in {"http", "https"} and not is_about_blank:
            raise ValueError("page_url must use HTTP(S) or be about:blank")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("page_url must not contain credentials")
        if self.navigation_timeout_seconds <= 0 or self.capture_timeout_seconds <= 0:
            raise ValueError("Timeouts must be positive")
        if self.post_capture_settle_seconds < 0:
            raise ValueError("post_capture_settle_seconds must not be negative")
        if self.viewport_width < 320 or self.viewport_height < 240:
            raise ValueError("Viewport is too small for a browser collection run")
        if self.proxy_server is not None:
            proxy = urlsplit(self.proxy_server)
            if proxy.scheme not in {"http", "https", "socks5"} or not proxy.hostname:
                raise ValueError("proxy_server must be an HTTP(S) or SOCKS5 URL")
            if proxy.username is not None or proxy.password is not None:
                raise ValueError("proxy_server credentials must not be embedded in the URL")
        if self.browser_channel not in {None, "chrome"}:
            raise ValueError("browser_channel must be 'chrome' or omitted")


class PlaywrightCaptureRunner:
    """Observe page-generated API responses without persistent browser state."""

    async def run(
        self,
        config: BrowserRunConfig,
        *,
        capture_rules: tuple[CaptureRule, ...],
    ) -> BrowserRunResult:
        started_at = datetime.now(UTC)
        capture = ResponseCapture(
            provider=config.provider,
            route_key=config.route_key,
            rules=capture_rules,
        )
        diagnostics: list[CaptureDiagnostic] = []
        screenshot_path: Path | None = None

        unknown_capture_names = config.expected_capture_names - {
            rule.name for rule in capture_rules
        }
        if unknown_capture_names:
            diagnostics.append(
                _diagnostic(
                    config,
                    FailureKind.INTERNAL_ERROR,
                    "Expected capture names are not present in the configured rules",
                    retryable=False,
                    details={"capture_names": ",".join(sorted(unknown_capture_names))},
                )
            )
            return _result(config, started_at, capture, diagnostics)

        if config.require_display_on_linux and not _headed_display_available():
            diagnostics.append(
                _diagnostic(
                    config,
                    FailureKind.BROWSER_UNAVAILABLE,
                    "Headed browser requires DISPLAY on Linux; run the worker under Xvfb",
                    retryable=False,
                )
            )
            return _result(config, started_at, capture, diagnostics)

        try:
            async_playwright, playwright_timeout = _load_playwright()
        except ModuleNotFoundError:
            diagnostics.append(
                _diagnostic(
                    config,
                    FailureKind.BROWSER_UNAVAILABLE,
                    "Playwright is not installed; install the collector optional dependency",
                    retryable=False,
                )
            )
            return _result(config, started_at, capture, diagnostics)

        pending_response_tasks: set[asyncio.Task[None]] = set()
        page: Any | None = None

        try:
            async with async_playwright() as playwright:
                launch_options: dict[str, Any] = {"headless": False}
                if config.proxy_server is not None:
                    launch_options["proxy"] = {"server": config.proxy_server}
                if config.browser_channel is not None:
                    launch_options["channel"] = config.browser_channel
                browser = await playwright.chromium.launch(**launch_options)
                try:
                    context = await browser.new_context(
                        locale=config.locale,
                        timezone_id=config.timezone_id,
                        viewport={
                            "width": config.viewport_width,
                            "height": config.viewport_height,
                        },
                    )
                    try:
                        page = await context.new_page()

                        def observe_response(response: Any) -> None:
                            task = asyncio.create_task(capture.handle(response))
                            pending_response_tasks.add(task)

                            def response_finished(finished_task: asyncio.Task[None]) -> None:
                                pending_response_tasks.discard(finished_task)
                                if finished_task.cancelled():
                                    return
                                exception = finished_task.exception()
                                if exception is not None:
                                    diagnostics.append(
                                        _diagnostic(
                                            config,
                                            FailureKind.INTERNAL_ERROR,
                                            "Response capture task failed",
                                            retryable=True,
                                            details={"exception_type": type(exception).__name__},
                                        )
                                    )

                            task.add_done_callback(response_finished)

                        try:
                            page.on("response", observe_response)
                            navigation_response = await page.goto(
                                config.page_url,
                                wait_until=config.wait_until,
                                timeout=config.navigation_timeout_seconds * 1000,
                            )
                            if (
                                navigation_response is not None
                                and navigation_response.status == 432
                            ):
                                diagnostics.append(
                                    _diagnostic(
                                        config,
                                        FailureKind.ANTI_BOT_432,
                                        "Provider returned HTTP 432 for the page navigation",
                                        status_code=432,
                                        retryable=True,
                                    )
                                )
                            elif config.expected_capture_names:
                                await capture.wait_until_terminal(
                                    config.expected_capture_names,
                                    timeout_seconds=config.capture_timeout_seconds,
                                )
                                if config.post_capture_settle_seconds > 0:
                                    await asyncio.sleep(config.post_capture_settle_seconds)
                        except playwright_timeout:
                            diagnostics.append(
                                _diagnostic(
                                    config,
                                    FailureKind.TIMEOUT,
                                    "Playwright navigation timed out",
                                    retryable=True,
                                )
                            )
                        except TimeoutError:
                            _append_missing_capture_diagnostics(config, capture, diagnostics)
                        except Exception as exc:  # noqa: BLE001 - Playwright exceptions vary
                            diagnostics.append(
                                _diagnostic(
                                    config,
                                    FailureKind.NAVIGATION_ERROR,
                                    "Browser collection run failed",
                                    retryable=True,
                                    details={"exception_type": type(exc).__name__},
                                )
                            )

                        current_diagnostics = [*capture.diagnostics, *diagnostics]
                        current_result = _result(
                            config,
                            started_at,
                            capture,
                            current_diagnostics,
                        )
                        if not current_result.success and config.screenshot_directory is not None:
                            screenshot_path = await _take_failure_screenshot(
                                page,
                                config,
                                diagnostics,
                            )
                    finally:
                        await context.close()
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001 - Playwright exposes multiple exception types
            kind, retryable = _classify_browser_exception(exc)
            diagnostics.append(
                _diagnostic(
                    config,
                    kind,
                    "Browser collection run failed",
                    retryable=retryable,
                    details={"exception_type": type(exc).__name__},
                )
            )
        finally:
            for task in pending_response_tasks:
                task.cancel()
            if pending_response_tasks:
                await asyncio.gather(*pending_response_tasks, return_exceptions=True)

        all_diagnostics = [*capture.diagnostics, *diagnostics]
        return _result(
            config,
            started_at,
            capture,
            all_diagnostics,
            screenshot_path=screenshot_path,
        )


def _load_playwright() -> tuple[Any, type[BaseException]]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    return async_playwright, PlaywrightTimeoutError


def _headed_display_available() -> bool:
    return platform.system() != "Linux" or bool(os.environ.get("DISPLAY"))


def _append_missing_capture_diagnostics(
    config: BrowserRunConfig,
    capture: ResponseCapture,
    diagnostics: list[CaptureDiagnostic],
) -> None:
    captured_names = {item.capture_name for item in capture.captures}
    for name in sorted(config.expected_capture_names - captured_names):
        if capture.has_terminal_failure_for(name):
            continue
        diagnostics.append(
            _diagnostic(
                config,
                FailureKind.TIMEOUT,
                "Expected page response was not captured before the deadline",
                capture_name=name,
                retryable=True,
            )
        )


async def _take_failure_screenshot(
    page: Any,
    config: BrowserRunConfig,
    diagnostics: list[CaptureDiagnostic],
) -> Path | None:
    assert config.screenshot_directory is not None
    config.screenshot_directory.mkdir(parents=True, exist_ok=True)
    route_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", config.route_key).strip("-") or "route"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = config.screenshot_directory / f"{config.provider}-{route_slug}-{timestamp}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception as exc:  # noqa: BLE001 - screenshot support depends on page state
        diagnostics.append(
            _diagnostic(
                config,
                FailureKind.SCREENSHOT_FAILED,
                "Failed to write diagnostic screenshot",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            )
        )
        return None
    return path


def _diagnostic(
    config: BrowserRunConfig,
    kind: FailureKind,
    message: str,
    *,
    capture_name: str | None = None,
    status_code: int | None = None,
    retryable: bool,
    details: dict[str, str | int | bool | None] | None = None,
) -> CaptureDiagnostic:
    return CaptureDiagnostic(
        kind=kind,
        message=message,
        provider=config.provider,
        route_key=config.route_key,
        capture_name=capture_name,
        status_code=status_code,
        retryable=retryable,
        details={
            **(details or {}),
            "browser_channel": config.browser_channel or "chromium",
        },
    )


def _classify_browser_exception(exc: Exception) -> tuple[FailureKind, bool]:
    message = str(exc).casefold()
    unavailable_markers = (
        "executable doesn't exist",
        "please run the following command to download",
        "chromium distribution 'chrome' is not found",
        "google chrome is not found",
        "missing x server",
        "browser has been closed",
    )
    if any(marker in message for marker in unavailable_markers):
        return FailureKind.BROWSER_UNAVAILABLE, False
    return FailureKind.NAVIGATION_ERROR, True


def _result(
    config: BrowserRunConfig,
    started_at: datetime,
    capture: ResponseCapture,
    diagnostics: list[CaptureDiagnostic],
    *,
    screenshot_path: Path | None = None,
) -> BrowserRunResult:
    return BrowserRunResult(
        provider=config.provider,
        route_key=config.route_key,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        captures=capture.captures,
        diagnostics=tuple(diagnostics),
        expected_capture_names=config.expected_capture_names,
        screenshot_path=screenshot_path,
    )

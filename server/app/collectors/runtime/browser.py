"""Async Playwright runner using isolated browser contexts."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
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

_CDP_TOTAL_BUFFER_BYTES = 128 * 1024 * 1024
_CDP_RESOURCE_BUFFER_BYTES = 64 * 1024 * 1024


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
    headless: bool = False
    background: bool = False

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

        if (
            not config.headless
            and config.require_display_on_linux
            and not _headed_display_available()
        ):
            diagnostics.append(
                _diagnostic(
                    config,
                    FailureKind.BROWSER_UNAVAILABLE,
                    (
                        "Headed browser mode requires DISPLAY on Linux; run the worker "
                        "under Xvfb or enable headless mode"
                    ),
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
        cdp_capture: _CdpResponseBodyCapture | None = None

        try:
            async with (
                async_playwright() as playwright,
                _browser_session(playwright, config) as (
                    browser,
                    persistent_context,
                ),
            ):
                context = persistent_context
                close_context = False
                if context is None:
                    context = await browser.new_context(**_browser_context_options(config))
                    close_context = True
                assert context is not None
                try:
                    page = (
                        context.pages[0]
                        if persistent_context is not None and context.pages
                        else await context.new_page()
                    )
                    if persistent_context is not None:
                        await page.set_viewport_size(
                            {
                                "width": config.viewport_width,
                                "height": config.viewport_height,
                            }
                        )
                    cdp_capture = await _start_cdp_response_body_capture(
                        context,
                        page,
                        capture=capture,
                        capture_rules=capture_rules,
                    )

                    def observe_response(response: Any) -> None:
                        task = asyncio.create_task(capture.handle(response))
                        pending_response_tasks.add(task)

                        def response_finished(
                            finished_task: asyncio.Task[None],
                        ) -> None:
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
                                        details={
                                            "exception_type": type(exception).__name__
                                        },
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
                        if navigation_response is not None and navigation_response.status == 432:
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

                    if cdp_capture is not None:
                        await cdp_capture.drain()
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
                    if cdp_capture is not None:
                        await cdp_capture.close()
                    if close_context:
                        await context.close()
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


@asynccontextmanager
async def _browser_session(
    playwright: Any,
    config: BrowserRunConfig,
) -> AsyncIterator[tuple[Any, Any | None]]:
    if _uses_hidden_macos_chrome(config):
        async with _hidden_macos_chrome_session(playwright, config) as session:
            yield session
        return

    launch_options: dict[str, Any] = {"headless": config.headless}
    if config.proxy_server is not None:
        launch_options["proxy"] = {"server": config.proxy_server}
    if config.browser_channel is not None:
        launch_options["channel"] = config.browser_channel
    browser = await playwright.chromium.launch(**launch_options)
    try:
        yield browser, None
    finally:
        await browser.close()


def _uses_hidden_macos_chrome(config: BrowserRunConfig) -> bool:
    return (
        platform.system() == "Darwin"
        and config.browser_channel == "chrome"
        and not config.headless
        and config.background
    )


@asynccontextmanager
async def _hidden_macos_chrome_session(
    playwright: Any,
    config: BrowserRunConfig,
) -> AsyncIterator[tuple[Any, Any]]:
    """Launch standard Chrome hidden and attach through its isolated CDP profile."""

    profile_directory = Path(tempfile.mkdtemp(prefix="farescope-chrome-"))
    browser: Any | None = None
    try:
        command = [
            "/usr/bin/open",
            "-gj",
            "-n",
            "-a",
            "Google Chrome",
            "--args",
            f"--user-data-dir={profile_directory}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            f"--lang={config.locale}",
            f"--window-size={config.viewport_width},{config.viewport_height}",
            "--window-position=-32000,-32000",
            "about:blank",
        ]
        if config.proxy_server is not None:
            command.insert(-1, f"--proxy-server={config.proxy_server}")
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError("hidden macOS Chrome launch failed")

        port = await _wait_for_devtools_port(profile_directory / "DevToolsActivePort")
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=15_000,
        )
        if not browser.contexts:
            raise RuntimeError("hidden macOS Chrome did not expose a browser context")
        yield browser, browser.contexts[0]
    finally:
        if browser is not None:
            with suppress(Exception):  # Best-effort cleanup after browser failure.
                await browser.close()
        # Closing a CDP connection does not terminate an externally launched Chrome.
        await _terminate_hidden_macos_chrome(profile_directory)
        shutil.rmtree(profile_directory, ignore_errors=True)


async def _wait_for_devtools_port(path: Path, *, timeout_seconds: float = 15.0) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            port = int(first_line)
            if 0 < port < 65_536:
                return port
        except (FileNotFoundError, IndexError, ValueError):
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError("hidden macOS Chrome DevTools endpoint was not ready")


async def _terminate_hidden_macos_chrome(profile_directory: Path) -> None:
    process_pattern = f"--user-data-dir={profile_directory}"
    await asyncio.to_thread(
        subprocess.run,
        [
            "/usr/bin/pkill",
            "-TERM",
            "-f",
            "--",
            process_pattern,
        ],
        check=False,
        capture_output=True,
        timeout=5,
    )
    for _ in range(50):
        matched = await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/pgrep", "-f", "--", process_pattern],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if matched.returncode == 1:
            return
        await asyncio.sleep(0.1)
    await asyncio.to_thread(
        subprocess.run,
        ["/usr/bin/pkill", "-KILL", "-f", "--", process_pattern],
        check=False,
        capture_output=True,
        timeout=5,
    )


@dataclass(frozen=True, slots=True)
class _CapturedRequest:
    method: str


@dataclass(frozen=True, slots=True)
class _CapturedJsonResponse:
    url: str
    status: int
    request: _CapturedRequest
    payload: Any

    async def json(self) -> Any:
        return self.payload


class _CdpResponseBodyCapture:
    """Retain only matched large response bodies in a bounded CDP buffer."""

    def __init__(
        self,
        session: Any,
        *,
        capture: ResponseCapture,
        capture_rules: tuple[CaptureRule, ...],
    ) -> None:
        self._session = session
        self._capture = capture
        self._capture_rules = capture_rules
        self._requests: dict[str, tuple[str, str]] = {}
        self._responses: dict[str, tuple[str, str, int]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._session.on("Network.requestWillBeSent", self._on_request)
        self._session.on("Network.responseReceived", self._on_response)
        self._session.on("Network.loadingFinished", self._on_loading_finished)
        self._session.on("Network.loadingFailed", self._on_loading_failed)
        await self._session.send(
            "Network.enable",
            {
                "maxTotalBufferSize": _CDP_TOTAL_BUFFER_BYTES,
                "maxResourceBufferSize": _CDP_RESOURCE_BUFFER_BYTES,
                "enableDurableMessages": True,
            },
        )

    def _on_request(self, event: Mapping[str, Any]) -> None:
        request_id = str(event.get("requestId", ""))
        request = event.get("request")
        if not request_id or not isinstance(request, Mapping):
            return
        url = str(request.get("url", ""))
        method = str(request.get("method", "GET")).upper()
        self._requests[request_id] = (url, method)

    def _on_response(self, event: Mapping[str, Any]) -> None:
        request_id = str(event.get("requestId", ""))
        response = event.get("response")
        request = self._requests.get(request_id)
        if request is None or not isinstance(response, Mapping):
            return
        url, method = request
        if not any(rule.matches(url, method) for rule in self._capture_rules):
            return
        try:
            status = int(response.get("status", 0))
        except (TypeError, ValueError):
            status = 0
        self._responses[request_id] = (url, method, status)

    def _on_loading_finished(self, event: Mapping[str, Any]) -> None:
        request_id = str(event.get("requestId", ""))
        response = self._responses.pop(request_id, None)
        self._requests.pop(request_id, None)
        if response is None:
            return
        task = asyncio.create_task(self._capture_response_body(request_id, response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_loading_failed(self, event: Mapping[str, Any]) -> None:
        request_id = str(event.get("requestId", ""))
        self._requests.pop(request_id, None)
        self._responses.pop(request_id, None)

    async def _capture_response_body(
        self,
        request_id: str,
        response: tuple[str, str, int],
    ) -> None:
        try:
            body_result = await self._session.send(
                "Network.getResponseBody",
                {"requestId": request_id},
            )
            raw_body = body_result.get("body")
            if not isinstance(raw_body, str):
                return
            if body_result.get("base64Encoded"):
                raw_body = base64.b64decode(raw_body, validate=True).decode("utf-8")
            payload = json.loads(raw_body)
        except Exception:  # noqa: BLE001 - Playwright capture remains the primary path
            return
        url, method, status = response
        await self._capture.handle(
            _CapturedJsonResponse(
                url=url,
                status=status,
                request=_CapturedRequest(method=method),
                payload=payload,
            )
        )

    async def drain(self, *, timeout_seconds: float = 5.0) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(tuple(self._tasks), timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        await self.drain()
        with suppress(Exception):
            await self._session.detach()


async def _start_cdp_response_body_capture(
    context: Any,
    page: Any,
    *,
    capture: ResponseCapture,
    capture_rules: tuple[CaptureRule, ...],
) -> _CdpResponseBodyCapture | None:
    if not capture_rules or not hasattr(context, "new_cdp_session"):
        return None
    session: Any | None = None
    try:
        session = await context.new_cdp_session(page)
        body_capture = _CdpResponseBodyCapture(
            session,
            capture=capture,
            capture_rules=capture_rules,
        )
        await body_capture.start()
        return body_capture
    except Exception:  # noqa: BLE001 - ordinary Playwright response capture remains available
        if session is not None:
            with suppress(Exception):
                await session.detach()
        return None


def _browser_context_options(config: BrowserRunConfig) -> dict[str, Any]:
    options: dict[str, Any] = {
        "locale": config.locale,
        "timezone_id": config.timezone_id,
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
    }
    return options


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
            "headless": config.headless,
            "background": config.background,
            "browser_mode": (
                "headless"
                if config.headless
                else "headed_background"
                if config.background
                else "headed"
            ),
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
        "hidden macos chrome launch failed",
        "did not expose a browser context",
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

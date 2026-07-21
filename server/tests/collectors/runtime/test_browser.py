from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.collectors.runtime import browser as browser_module
from app.collectors.runtime.browser import BrowserRunConfig, PlaywrightCaptureRunner
from app.collectors.runtime.capture import ResponseCapture, ctrip_capture_rules
from app.collectors.runtime.models import FailureKind


class FakePlaywrightTimeout(Exception):
    pass


class FakeRequest:
    method = "POST"


class FakeResponse:
    def __init__(self, *, url: str, status: int = 200, payload: Any = None) -> None:
        self.url = url
        self.status = status
        self.request = FakeRequest()
        self._payload = payload

    async def json(self) -> Any:
        return self._payload


class FakePage:
    def __init__(self, api_response: FakeResponse | None) -> None:
        self.api_response = api_response
        self.response_handler: Any = None
        self.screenshot_calls: list[dict[str, Any]] = []

    def on(self, event: str, handler: Any) -> None:
        assert event == "response"
        self.response_handler = handler

    async def goto(self, _url: str, **_kwargs: Any) -> FakeResponse:
        if self.api_response is not None:
            self.response_handler(self.api_response)
        return FakeResponse(url="https://flights.ctrip.com/page", payload={})

    async def screenshot(self, **kwargs: Any) -> None:
        self.screenshot_calls.append(kwargs)
        Path(kwargs["path"]).touch()


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False
        self.init_scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.context_kwargs: dict[str, Any] | None = None
        self.context: FakeContext | None = None
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_kwargs = kwargs
        self.context = FakeContext(self.page)
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: dict[str, Any] | None = None

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakePlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, *_args: Any) -> None:
        return None


def fake_loader(api_response: FakeResponse | None) -> tuple[Any, FakeBrowser, FakeChromium]:
    page = FakePage(api_response)
    browser = FakeBrowser(page)
    chromium = FakeChromium(browser)
    playwright = FakePlaywright(chromium)
    return lambda: FakePlaywrightManager(playwright), browser, chromium


@pytest.mark.asyncio
async def test_runner_uses_verified_headed_ephemeral_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_playwright, browser, chromium = fake_loader(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch?token=redacted",
            payload={"status": 0, "data": {"flightItineraryList": []}},
        )
    )
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"batch_search"}),
            capture_timeout_seconds=0.1,
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is True
    assert chromium.launch_kwargs is not None
    assert chromium.launch_kwargs == {"headless": False}
    assert browser.context_kwargs is not None
    assert browser.context_kwargs["locale"] == "zh-CN"
    assert browser.context_kwargs["timezone_id"] == "Asia/Shanghai"
    assert "storage_state" not in browser.context_kwargs
    assert "user_data_dir" not in browser.context_kwargs
    assert browser.context is not None
    assert browser.context.init_scripts == []
    assert result.captures[0].payload["status"] == 0
    assert browser.closed is True


@pytest.mark.asyncio
async def test_runner_settles_after_first_capture_for_async_pull_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_playwright, _browser, _chromium = fake_loader(
        FakeResponse(
            url="https://flights.ctrip.com/international/search/api/search/batchSearch",
            payload={"status": 0, "data": {}},
        )
    )
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)
    monkeypatch.setattr(browser_module.asyncio, "sleep", fake_sleep)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"batch_search"}),
            capture_timeout_seconds=0.1,
            post_capture_settle_seconds=1.5,
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is True
    assert sleep_calls == [1.5]


@pytest.mark.asyncio
async def test_runner_passes_explicit_proxy_without_persisting_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_playwright, _browser, chromium = fake_loader(None)
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="local",
            route_key="proxy-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            proxy_server="http://127.0.0.1:7890",
        ),
        capture_rules=(),
    )

    assert chromium.launch_kwargs == {
        "headless": False,
        "proxy": {"server": "http://127.0.0.1:7890"},
    }


@pytest.mark.asyncio
async def test_runner_can_use_explicit_system_chrome_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_playwright, _browser, chromium = fake_loader(None)
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="local",
            route_key="chrome-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            browser_channel="chrome",
        ),
        capture_rules=(),
    )

    assert chromium.launch_kwargs == {
        "headless": False,
        "channel": "chrome",
    }


@pytest.mark.asyncio
async def test_runner_can_use_headed_mode_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_playwright, _browser, chromium = fake_loader(None)
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="local",
            route_key="headless-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            headless=False,
        ),
        capture_rules=(),
    )

    assert chromium.launch_kwargs == {"headless": False}


def test_background_headed_chrome_uses_hidden_macos_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_module.platform, "system", lambda: "Darwin")

    assert browser_module._uses_hidden_macos_chrome(
        BrowserRunConfig(
            provider="local",
            route_key="hidden-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            browser_channel="chrome",
            headless=False,
            background=True,
        )
    )
    assert not browser_module._uses_hidden_macos_chrome(
        BrowserRunConfig(
            provider="local",
            route_key="headless-smoke",
            page_url="about:blank",
            expected_capture_names=frozenset(),
            browser_channel="chrome",
            headless=True,
            background=True,
        )
    )


@pytest.mark.asyncio
async def test_hidden_macos_session_always_terminates_external_chrome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_directory = tmp_path / "farescope-chrome-test"
    profile_directory.mkdir()
    commands: list[list[str]] = []

    class CdpBrowser:
        def __init__(self) -> None:
            self.contexts = [object()]
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class CdpChromium:
        def __init__(self, browser: CdpBrowser) -> None:
            self.browser = browser

        async def connect_over_cdp(self, _endpoint: str, **_kwargs: Any) -> CdpBrowser:
            return self.browser

    def fake_run(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        return type(
            "Completed",
            (),
            {"returncode": 1 if command[0] == "/usr/bin/pgrep" else 0},
        )()

    async def fake_devtools_port(_path: Path, **_kwargs: Any) -> int:
        return 9222

    browser = CdpBrowser()
    playwright = type("Playwright", (), {"chromium": CdpChromium(browser)})()
    monkeypatch.setattr(browser_module.tempfile, "mkdtemp", lambda **_kwargs: profile_directory)
    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)
    monkeypatch.setattr(browser_module, "_wait_for_devtools_port", fake_devtools_port)

    config = BrowserRunConfig(
        provider="local",
        route_key="cleanup-smoke",
        page_url="about:blank",
        expected_capture_names=frozenset(),
        browser_channel="chrome",
        headless=False,
        background=True,
    )
    async with browser_module._hidden_macos_chrome_session(playwright, config):
        pass

    assert browser.closed is True
    assert commands[0][0] == "/usr/bin/open"
    assert [
        "/usr/bin/pkill",
        "-TERM",
        "-f",
        "--",
        f"--user-data-dir={profile_directory}",
    ] in commands
    assert commands[-1] == [
        "/usr/bin/pgrep",
        "-f",
        "--",
        f"--user-data-dir={profile_directory}",
    ]
    assert not profile_directory.exists()


@pytest.mark.asyncio
async def test_cdp_capture_retains_large_matched_response_body() -> None:
    class FakeCdpSession:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {}
            self.sent: list[tuple[str, dict[str, Any]]] = []
            self.detached = False

        def on(self, event: str, handler: Any) -> None:
            self.handlers[event] = handler

        async def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            self.sent.append((method, params))
            if method == "Network.getResponseBody":
                return {
                    "body": (
                        '{"status":0,"data":{"flightItineraryList":'
                        '[{"itineraryId":"round-trip"}]}}'
                    ),
                    "base64Encoded": False,
                }
            return {}

        async def detach(self) -> None:
            self.detached = True

    rules = ctrip_capture_rules()
    capture = ResponseCapture(provider="ctrip", route_key="SHA-TYO", rules=rules)
    session = FakeCdpSession()
    bridge = browser_module._CdpResponseBodyCapture(
        session,
        capture=capture,
        capture_rules=rules,
    )
    await bridge.start()

    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "large-round-trip",
            "request": {
                "url": "https://flights.ctrip.com/international/search/api/search/pull/id-0",
                "method": "POST",
            },
        }
    )
    session.handlers["Network.responseReceived"](
        {
            "requestId": "large-round-trip",
            "response": {"status": 200},
        }
    )
    session.handlers["Network.loadingFinished"]({"requestId": "large-round-trip"})
    await bridge.drain()
    await bridge.close()

    assert session.sent[0] == (
        "Network.enable",
        {
            "maxTotalBufferSize": browser_module._CDP_TOTAL_BUFFER_BYTES,
            "maxResourceBufferSize": browser_module._CDP_RESOURCE_BUFFER_BYTES,
            "enableDurableMessages": True,
        },
    )
    assert capture.captures[0].capture_name == "batch_search"
    assert capture.captures[0].payload["data"]["flightItineraryList"][0] == {
        "itineraryId": "round-trip"
    }
    assert session.detached is True


def test_proxy_url_rejects_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="credentials"):
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset(),
            proxy_server="http://user:secret@127.0.0.1:7890",
        )


def test_browser_channel_rejects_unapproved_binaries() -> None:
    with pytest.raises(ValueError, match="browser_channel"):
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset(),
            browser_channel="msedge",
        )


@pytest.mark.asyncio
async def test_success_does_not_write_opt_in_screenshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async_playwright, browser, _chromium = fake_loader(
        FakeResponse(
            url="https://flights.ctrip.com/api/batchSearch",
            payload={"status": 0, "data": {}},
        )
    )
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"batch_search"}),
            screenshot_directory=tmp_path,
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is True
    assert result.screenshot_path is None
    assert browser.page.screenshot_calls == []


@pytest.mark.asyncio
async def test_linux_without_display_fails_before_loading_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: False)
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"calendar"}),
            headless=False,
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is False
    assert result.diagnostics[0].kind == FailureKind.BROWSER_UNAVAILABLE
    assert "DISPLAY" in result.diagnostics[0].message


@pytest.mark.asyncio
async def test_missing_response_times_out_and_writes_opt_in_screenshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async_playwright, _browser, _chromium = fake_loader(None)
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"calendar"}),
            capture_timeout_seconds=0.001,
            screenshot_directory=tmp_path,
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is False
    assert result.diagnostics[0].kind == FailureKind.TIMEOUT
    assert result.screenshot_path is not None
    assert result.screenshot_path.exists()


@pytest.mark.asyncio
async def test_page_level_432_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    async_playwright, _browser, _chromium = fake_loader(None)
    manager = async_playwright()
    page = manager.playwright.chromium.browser.page

    async def goto_432(_url: str, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(url="https://flights.ctrip.com/page", status=432)

    page.goto = goto_432
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (async_playwright, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"calendar"}),
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is False
    assert result.diagnostics[0].kind == FailureKind.ANTI_BOT_432


@pytest.mark.asyncio
async def test_unknown_capture_name_fails_before_browser_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"unknown"}),
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is False
    assert result.diagnostics[0].kind == FailureKind.INTERNAL_ERROR


@pytest.mark.asyncio
async def test_missing_browser_binary_is_browser_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenManager:
        async def __aenter__(self) -> Any:
            raise RuntimeError("Executable doesn't exist; install Chromium")

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(
        browser_module,
        "_load_playwright",
        lambda: (BrokenManager, FakePlaywrightTimeout),
    )
    monkeypatch.setattr(browser_module, "_headed_display_available", lambda: True)

    result = await PlaywrightCaptureRunner().run(
        BrowserRunConfig(
            provider="ctrip",
            route_key="SHA-TYO",
            page_url="https://flights.ctrip.com/search",
            expected_capture_names=frozenset({"calendar"}),
        ),
        capture_rules=ctrip_capture_rules(),
    )

    assert result.success is False
    assert result.diagnostics[0].kind == FailureKind.BROWSER_UNAVAILABLE
    assert result.diagnostics[0].retryable is False

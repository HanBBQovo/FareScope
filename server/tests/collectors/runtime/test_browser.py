from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.collectors.runtime import browser as browser_module
from app.collectors.runtime.browser import BrowserRunConfig, PlaywrightCaptureRunner
from app.collectors.runtime.capture import ctrip_capture_rules
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
async def test_runner_uses_headed_ephemeral_context(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert chromium.launch_kwargs == {"headless": False}
    assert browser.context_kwargs is not None
    assert "storage_state" not in browser.context_kwargs
    assert "user_data_dir" not in browser.context_kwargs
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

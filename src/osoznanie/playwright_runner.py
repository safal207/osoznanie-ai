"""Optional real-browser runner for QA checks."""

from __future__ import annotations

from enum import StrEnum
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from .action_dispatcher import PermanentToolError, RetryableToolError


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class BrowserCheckCode(StrEnum):
    PASSED = "passed"
    ACTION_NOT_VISIBLE = "action_not_visible"
    ACTION_DISABLED = "action_disabled"
    EXPECTED_STATE_NOT_OBSERVED = "expected_state_not_observed"


class PlaywrightCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str = Field(min_length=1)
    target_url: AnyHttpUrl
    action_selector: str = Field(min_length=1)
    success_selector: str = Field(min_length=1)
    changed_components: list[str] = Field(min_length=1)
    browser: BrowserEngine = BrowserEngine.CHROMIUM
    timeout_ms: int = Field(default=5_000, ge=100, le=60_000)


class BrowserCheckEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: BrowserCheckCode
    passed: bool
    target: str
    duration_ms: int = Field(ge=0)


class PlaywrightBrowserCheckRunner:
    """Run one headless browser interaction through Playwright."""

    def run(self, request: PlaywrightCheckInput) -> BrowserCheckEvidence:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as error:
            raise PermanentToolError("playwright_not_installed") from error

        started = perf_counter()
        target = _safe_target(str(request.target_url))
        try:
            with sync_playwright() as playwright:
                browser_type = getattr(playwright, request.browser.value)
                browser = browser_type.launch(headless=True)
                try:
                    page = browser.new_page()
                    try:
                        page.goto(
                            str(request.target_url),
                            wait_until="domcontentloaded",
                            timeout=request.timeout_ms,
                        )
                    except PlaywrightTimeoutError as error:
                        raise RetryableToolError(
                            "playwright_navigation_timeout"
                        ) from error

                    action = page.locator(request.action_selector).first
                    try:
                        action.wait_for(state="visible", timeout=request.timeout_ms)
                    except PlaywrightTimeoutError:
                        return _evidence(
                            BrowserCheckCode.ACTION_NOT_VISIBLE,
                            target,
                            started,
                        )
                    if not action.is_enabled():
                        return _evidence(
                            BrowserCheckCode.ACTION_DISABLED,
                            target,
                            started,
                        )

                    action.click(timeout=request.timeout_ms)
                    success = page.locator(request.success_selector).first
                    try:
                        success.wait_for(state="visible", timeout=request.timeout_ms)
                    except PlaywrightTimeoutError:
                        return _evidence(
                            BrowserCheckCode.EXPECTED_STATE_NOT_OBSERVED,
                            target,
                            started,
                        )
                    return _evidence(BrowserCheckCode.PASSED, target, started)
                finally:
                    browser.close()
        except (RetryableToolError, PermanentToolError):
            raise
        except PlaywrightError as error:
            raise RetryableToolError("playwright_runtime_error") from error


def _evidence(
    code: BrowserCheckCode,
    target: str,
    started: float,
) -> BrowserCheckEvidence:
    return BrowserCheckEvidence(
        code=code,
        passed=code is BrowserCheckCode.PASSED,
        target=target,
        duration_ms=max(0, round((perf_counter() - started) * 1_000)),
    )


def _safe_target(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

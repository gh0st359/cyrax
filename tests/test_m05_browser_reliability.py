"""
M05 regression tests — browser and tool reliability.

Tests cover:
- Browser unavailable: preflight returns useful message, no crash
- Browser command validation: invalid methods fail fast with actionable error
- parse_browser_command_with_error: actionable error messages on bad syntax
- Orchestrator handles browser unavailability without crashing
- Mocked browser goto/fill/content roundtrip
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from tools.browser import (
    BrowserManager,
    BrowserResult,
    BROWSER_COMMANDS,
    parse_browser_command,
    parse_browser_command_with_error,
    validate_browser_command,
    is_browser_command,
)


# ── Preflight ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_preflight_returns_false_when_playwright_missing(monkeypatch):
    """preflight() must return (False, message) if playwright is not importable."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright":
            raise ImportError("no module named playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ok, msg = BrowserManager.preflight()
    assert not ok
    assert "playwright" in msg.lower() or "install" in msg.lower()


@pytest.mark.unit
def test_browser_available_method_wraps_preflight(monkeypatch):
    """available() must return True when preflight passes, False otherwise."""
    monkeypatch.setattr(BrowserManager, "preflight",
                        staticmethod(lambda: (True, "ok")))
    bm = BrowserManager.__new__(BrowserManager)
    assert bm.available() is True

    monkeypatch.setattr(BrowserManager, "preflight",
                        staticmethod(lambda: (False, "no chromium")))
    assert bm.available() is False


# ── Command parse and validation ───────────────────────────────────────────────


@pytest.mark.unit
def test_validate_browser_command_catches_missing_required_arg():
    err = validate_browser_command("goto", [], {})
    assert err is not None
    assert "goto" in err.lower() or "url" in err.lower()


@pytest.mark.unit
def test_validate_browser_command_catches_unexpected_kwarg():
    err = validate_browser_command("screenshot", [], {"nonexistent_flag": True})
    assert err is not None
    assert "nonexistent_flag" in err


@pytest.mark.unit
def test_validate_browser_command_passes_for_valid_call():
    err = validate_browser_command("goto", ["https://example.com"], {})
    assert err is None


@pytest.mark.unit
def test_parse_browser_command_with_error_unknown_method():
    result, error = parse_browser_command_with_error("browser.nonexistent()")
    assert result is None
    assert error is not None
    assert "nonexistent" in error


@pytest.mark.unit
def test_parse_browser_command_with_error_missing_paren():
    result, error = parse_browser_command_with_error('browser.goto("https://example.com"')
    assert result is None
    assert error is not None
    assert "closing" in error.lower() or "parenthes" in error.lower()


@pytest.mark.unit
def test_parse_browser_command_with_error_not_a_browser_call():
    result, error = parse_browser_command_with_error("curl https://example.com")
    assert result is None
    assert error is not None


@pytest.mark.unit
def test_parse_browser_command_with_error_success():
    result, error = parse_browser_command_with_error('browser.goto("https://example.com")')
    assert result is not None
    assert error is None
    method, args, kwargs = result
    assert method == "goto"
    assert args == ["https://example.com"]


# ── Mocked browser method roundtrips ──────────────────────────────────────────


@pytest.mark.integration
def test_browser_goto_returns_browser_result(tmp_path):
    """With a mocked Playwright page, browser.goto() must return a BrowserResult."""
    bm = BrowserManager.__new__(BrowserManager)
    bm.headless = True
    bm.work_dir = tmp_path
    bm.screenshots_dir = tmp_path / "screenshots"
    bm.screenshots_dir.mkdir()
    bm._screenshot_counter = 0
    bm._started = True  # Skip _ensure_started

    fake_page = MagicMock()
    fake_page.goto.return_value = MagicMock(status=200)
    fake_page.title.return_value = "Test Page"
    fake_page.url = "https://example.com"

    bm._page = fake_page

    result = bm.goto("https://example.com")
    assert isinstance(result, BrowserResult)
    assert result.success
    assert "200" in result.data or "navigated" in result.data.lower()


@pytest.mark.integration
def test_browser_goto_handles_exception(tmp_path):
    """When Playwright raises, goto() must return a failed BrowserResult — not crash."""
    bm = BrowserManager.__new__(BrowserManager)
    bm.headless = True
    bm.work_dir = tmp_path
    bm.screenshots_dir = tmp_path / "screenshots"
    bm.screenshots_dir.mkdir()
    bm._screenshot_counter = 0
    bm._started = True

    fake_page = MagicMock()
    fake_page.goto.side_effect = Exception("net::ERR_NAME_NOT_RESOLVED")
    bm._page = fake_page

    result = bm.goto("https://does-not-exist.invalid")
    assert isinstance(result, BrowserResult)
    assert not result.success
    assert "err_name_not_resolved" in result.error.lower()


@pytest.mark.integration
def test_browser_fill_mocked(tmp_path):
    """browser.fill() with mocked page must return success."""
    bm = BrowserManager.__new__(BrowserManager)
    bm._started = True
    bm.work_dir = tmp_path
    bm.screenshots_dir = tmp_path / "screenshots"
    bm.screenshots_dir.mkdir()
    bm._screenshot_counter = 0

    fake_page = MagicMock()
    fake_page.url = "https://example.com/login"
    bm._page = fake_page

    result = bm.fill("#username", "admin")
    assert isinstance(result, BrowserResult)
    assert result.success


@pytest.mark.integration
def test_browser_content_mocked(tmp_path):
    """browser.content() with mocked page must truncate output."""
    bm = BrowserManager.__new__(BrowserManager)
    bm._started = True
    bm.work_dir = tmp_path
    bm.screenshots_dir = tmp_path / "screenshots"
    bm.screenshots_dir.mkdir()
    bm._screenshot_counter = 0

    fake_page = MagicMock()
    fake_page.url = "https://example.com"
    fake_page.content.return_value = "<html>" + "x" * 50000 + "</html>"
    bm._page = fake_page

    result = bm.content()
    assert isinstance(result, BrowserResult)
    assert result.success
    # Output should be capped — not 50K characters
    assert len(result.data) < 20000


# ── is_browser_command detection ──────────────────────────────────────────────


@pytest.mark.unit
def test_is_browser_command_positive():
    assert is_browser_command("browser.goto('https://example.com')")
    assert is_browser_command("browser.screenshot()")
    assert is_browser_command("browser.fill('#id', 'val')")


@pytest.mark.unit
def test_is_browser_command_negative():
    assert not is_browser_command("curl https://example.com")
    assert not is_browser_command("nmap -sV target")
    assert not is_browser_command("browser")  # missing method call

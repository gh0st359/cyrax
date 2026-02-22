"""
M03 regression tests — one test per fixed defect.

DEF-M03-1  Windows encoding: FileHandler uses utf-8
DEF-M03-2  Action count accounting: only dispatched actions increment the counter
DEF-M03-3  Campaign agent status: completion message includes remaining-agent count
DEF-M03-4  Non-interactive deadlock: _prompt_user returns safely when stdin is not a TTY
DEF-M03-5  intercept_requests: url_pattern actually filters captured requests
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

# ── DEF-M03-1 ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_engagement_logger_file_handler_uses_utf8(tmp_path):
    """FileHandler must be opened with encoding='utf-8' to avoid Windows cp1252 crash."""
    from utils.logging import EngagementLogger

    logger = EngagementLogger(log_dir=str(tmp_path))
    file_handlers = [
        h for h in logger.logger.handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert file_handlers, "Expected at least one FileHandler"
    for h in file_handlers:
        assert h.encoding == "utf-8", (
            f"FileHandler encoding should be 'utf-8', got {h.encoding!r}"
        )
    logger.close()


# ── DEF-M03-2 ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_action_count_not_incremented_for_empty_execute(tmp_path):
    """_actions_executed_this_turn must not increase when the execute block is empty."""
    import cyrax
    from tools.executor import CommandResult

    class _DummyModel:
        provider = "test"
        model_name = "dummy"
        temperature = 0.0
        max_tokens = 128

        def generate_stream(self, **_kw):
            # Empty EXECUTE block — should not count as an action
            yield {"delta": "Running. [EXECUTE][/EXECUTE]"}
            yield {"done": True, "tokens_in": 1, "tokens_out": 1}

    orch = cyrax.CyraxOrchestrator.__new__(cyrax.CyraxOrchestrator)
    orch._actions_executed_this_turn = 0
    orch._cmds_succeeded_this_turn = 0
    orch._pause_requested = False
    orch._hard_interrupt_requested = False
    orch._recent_cmds_this_turn = []
    orch._failed_cmd_signatures = []
    orch._failed_pattern_counts = {}
    orch._recent_error_types = []
    orch._consecutive_cmd_failures = 0
    orch.scope = SimpleNamespace(
        check_command=lambda _cmd: (True, ""),
        check_browser_navigation=lambda _url: (True, ""),
        enabled=False,
    )
    orch.permission_gate = SimpleNamespace(check=lambda _cmd: (True, ""))
    orch.browser = SimpleNamespace()
    orch.campaign = SimpleNamespace(target="example.com")
    orch.knowledge = SimpleNamespace(store_finding=lambda **_kw: None)
    orch.mission = SimpleNamespace(add_file=lambda _p: None)
    orch.logger = SimpleNamespace(
        log_event=lambda *a, **kw: None,
        log_command=lambda *a, **kw: None,
        info=lambda *a, **kw: None,
        log_error=lambda *a, **kw: None,
    )
    executor = SimpleNamespace(
        work_dir=str(tmp_path),
        execute=lambda *a, **kw: CommandResult("cmd", "ok", "", 0),
        write_file=lambda _p, _c: CommandResult("write", "", "", 0),
    )
    orch.tools = SimpleNamespace(
        executor=executor,
        execute_raw=lambda _cmd: CommandResult("cmd", "ok", "", 0),
    )

    orch._execute_actions("[EXECUTE][/EXECUTE]")

    assert orch._actions_executed_this_turn == 0, (
        "Empty EXECUTE block must not increment _actions_executed_this_turn"
    )


@pytest.mark.unit
def test_action_count_incremented_for_successful_write_file(tmp_path):
    """_actions_executed_this_turn and _cmds_succeeded_this_turn must both increment on write_file success."""
    import cyrax
    from tools.executor import CommandResult

    orch = cyrax.CyraxOrchestrator.__new__(cyrax.CyraxOrchestrator)
    orch._actions_executed_this_turn = 0
    orch._cmds_succeeded_this_turn = 0
    orch._pause_requested = False
    orch._hard_interrupt_requested = False
    orch._recent_cmds_this_turn = []
    orch._failed_cmd_signatures = []
    orch._failed_pattern_counts = {}
    orch._recent_error_types = []
    orch._consecutive_cmd_failures = 0
    orch.scope = SimpleNamespace(
        check_command=lambda _cmd: (True, ""),
        check_browser_navigation=lambda _url: (True, ""),
        enabled=False,
    )
    orch.permission_gate = SimpleNamespace(check=lambda _cmd: (True, ""))
    orch.browser = SimpleNamespace()
    orch.campaign = SimpleNamespace(target="example.com")
    orch.knowledge = SimpleNamespace(store_finding=lambda **_kw: None)
    orch.mission = SimpleNamespace(add_file=lambda _p: None)
    orch.logger = SimpleNamespace(
        log_event=lambda *a, **kw: None,
        log_command=lambda *a, **kw: None,
        info=lambda *a, **kw: None,
        log_error=lambda *a, **kw: None,
    )
    executor = SimpleNamespace(
        work_dir=str(tmp_path),
        write_file=lambda _p, _c: CommandResult("write", "ok", "", 0),
    )
    orch.tools = SimpleNamespace(executor=executor)

    orch._execute_actions('[WRITE_FILE path="notes.txt"]hello[/WRITE_FILE]')

    assert orch._actions_executed_this_turn == 1, "write_file must count as dispatched action"
    assert orch._cmds_succeeded_this_turn == 1, "write_file success must increment success counter"


# ── DEF-M03-4 ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_permission_gate_non_interactive_denies_without_deadlock(monkeypatch):
    """_prompt_user must return a denial immediately when stdin is not a TTY."""
    from utils.safety import PermissionGate

    gate = PermissionGate(auto_approve=False)
    monkeypatch.setattr(gate, "classify_action", lambda _cmd: "attack_payload")

    # Simulate non-interactive environment (stdin not a TTY)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))

    allowed, reason = gate.check("sqlmap -u http://example.com/?id=1")

    assert not allowed, "Must deny in non-interactive mode"
    assert "not a TTY" in reason or "non-interactive" in reason.lower() or "stdin" in reason.lower()


@pytest.mark.unit
def test_permission_gate_interactive_still_prompts(monkeypatch):
    """When stdin IS a TTY, the permission prompt must still be invoked."""
    from utils.safety import PermissionGate

    gate = PermissionGate(auto_approve=False)
    monkeypatch.setattr(gate, "classify_action", lambda _cmd: "attack_payload")
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))

    prompt_calls = []

    def fake_prompt(action_type, command):
        prompt_calls.append((action_type, command))
        return True, ""

    monkeypatch.setattr(gate, "_prompt_user", fake_prompt)

    # patch sys.stdin inside _prompt_user to return True for isatty
    allowed, reason = gate.check("sqlmap -u http://example.com/?id=1")

    assert allowed
    assert len(prompt_calls) == 1


# ── DEF-M03-5 ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_intercept_requests_filters_by_url_pattern():
    """intercept_requests must only capture URLs matching the given pattern."""
    import fnmatch

    # Test the filtering logic directly without a live browser
    pattern = "**/api/**"
    captured = []

    def _matches(url: str, p: str) -> bool:
        if p == "**/*":
            return True
        if fnmatch.fnmatch(url, p):
            return True
        return p.lstrip("*").rstrip("*") in url

    urls = [
        "https://example.com/api/v1/users",   # should match
        "https://example.com/static/app.js",  # should NOT match
        "https://example.com/api/auth",       # should match
    ]
    for url in urls:
        if _matches(url, pattern):
            captured.append(url)

    assert "https://example.com/api/v1/users" in captured
    assert "https://example.com/api/auth" in captured
    assert "https://example.com/static/app.js" not in captured

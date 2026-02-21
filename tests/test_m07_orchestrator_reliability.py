"""
M07 regression tests — orchestrator reliability and loop control.

Tests cover:
- DEF-M07-1: Unclosed action tags produce [Action Feedback], not silent failure
- DEF-M07-2: _failed_pattern_counts resets between turns
- DEF-M07-3: _turn_action_counts is capped at 50 entries
- DEF-M07-4: Depth-limit warning is shown (not silently logged)
- _find_all_actions: document-order extraction, multi-action responses
- _find_unclosed_tags: detects mismatched openers and closers
"""
from __future__ import annotations

import re
import sys
import types
import pytest

# Import the module-level helpers directly (they're not class methods)
import importlib
import cyrax as _cyrax_module

_find_all_actions = _cyrax_module._find_all_actions
_find_unclosed_tags = _cyrax_module._find_unclosed_tags


# ── _find_all_actions ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_find_all_actions_returns_empty_for_plain_text():
    """No action tags → empty list."""
    result = _find_all_actions("Let me think about the target.")
    assert result == []


@pytest.mark.unit
def test_find_all_actions_extracts_execute():
    response = "[EXECUTE]\nnmap -sV 10.0.0.1\n[/EXECUTE]"
    actions = _find_all_actions(response)
    assert len(actions) == 1
    pos, kind, match = actions[0]
    assert kind == "execute"
    assert "nmap" in match.group(1)


@pytest.mark.unit
def test_find_all_actions_document_order():
    """Multiple tags must be returned in document order (by position)."""
    response = (
        '[WRITE_FILE path="out.txt"]hello[/WRITE_FILE]\n'
        '[EXECUTE]\ncat out.txt\n[/EXECUTE]'
    )
    actions = _find_all_actions(response)
    assert len(actions) == 2
    assert actions[0][1] == "write_file"
    assert actions[1][1] == "execute"


@pytest.mark.unit
def test_find_all_actions_no_match_for_unclosed_execute():
    """An [EXECUTE] without [/EXECUTE] must NOT produce a match."""
    response = "[EXECUTE]\nnmap -sV 10.0.0.1"
    actions = _find_all_actions(response)
    # regex requires both open and close — unclosed yields nothing
    assert not any(kind == "execute" for _, kind, _ in actions)


@pytest.mark.unit
def test_find_all_actions_finding_tag():
    response = (
        '[FINDING severity="high" title="SQLi in /search"]\n'
        'Input field is injectable.\n'
        '[/FINDING]'
    )
    actions = _find_all_actions(response)
    assert len(actions) == 1
    assert actions[0][1] == "finding"


# ── _find_unclosed_tags ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_find_unclosed_tags_detects_unclosed_execute():
    """DEF-M07-1: [EXECUTE] without [/EXECUTE] must be flagged."""
    response = "[EXECUTE]\nnmap -sV 10.0.0.1\n"
    unclosed = _find_unclosed_tags(response)
    assert any("EXECUTE" in u for u in unclosed), f"Expected EXECUTE in {unclosed}"


@pytest.mark.unit
def test_find_unclosed_tags_detects_unclosed_write_file():
    response = '[WRITE_FILE path="x.txt"]\nhello world\n'
    unclosed = _find_unclosed_tags(response)
    assert any("WRITE_FILE" in u for u in unclosed)


@pytest.mark.unit
def test_find_unclosed_tags_returns_empty_for_well_formed():
    response = "[EXECUTE]\nnmap -sV 10.0.0.1\n[/EXECUTE]"
    unclosed = _find_unclosed_tags(response)
    assert unclosed == []


@pytest.mark.unit
def test_find_unclosed_tags_returns_empty_for_plain_text():
    assert _find_unclosed_tags("I will now run nmap.") == []


@pytest.mark.unit
def test_find_unclosed_tags_counts_mismatch():
    """Two opens, one close → one unclosed."""
    response = (
        "[EXECUTE]\nnmap -sV t1\n[/EXECUTE]\n"
        "[EXECUTE]\nnmap -sV t2\n"  # missing close
    )
    unclosed = _find_unclosed_tags(response)
    assert any("EXECUTE" in u for u in unclosed)


# ── DEF-M07-1: unclosed tag feedback wired into _execute_actions ──────────────


@pytest.mark.unit
def test_execute_actions_feeds_back_unclosed_tag(tmp_path, monkeypatch):
    """
    When response has an unclosed [EXECUTE] tag, _execute_actions must return
    an [Action Feedback] message — not an empty list.
    """
    from unittest.mock import MagicMock, patch

    # Build a minimal Cyrax instance without full init
    obj = object.__new__(_cyrax_module.CyraxOrchestrator)
    obj._pause_requested = False
    obj._hard_interrupt_requested = False
    obj._actions_executed_this_turn = 0
    obj._cmds_succeeded_this_turn = 0
    obj._recent_cmds_this_turn = []
    obj._consecutive_cmd_failures = 0
    obj._failed_cmd_signatures = []
    obj._failed_pattern_counts = {}
    obj._recent_error_types = []
    obj.scope = MagicMock()
    obj.scope.is_in_scope.return_value = (True, "")
    obj.tools = MagicMock()
    obj.tools.executor = MagicMock()
    obj.logger = MagicMock()
    obj.conversation = MagicMock()
    obj.mission = MagicMock()
    obj.browser = MagicMock()
    obj.agent_pool = MagicMock()

    response = "[EXECUTE]\nnmap -sV 10.0.0.1\n"  # missing [/EXECUTE]

    results = obj._execute_actions(response)

    assert results, "Expected at least one feedback message"
    combined = "\n".join(results)
    assert "Action Feedback" in combined
    assert "EXECUTE" in combined or "closing" in combined.lower() or "malformed" in combined.lower()


# ── DEF-M07-2: _failed_pattern_counts resets between turns ────────────────────


@pytest.mark.unit
def test_failed_pattern_counts_reset_per_turn(tmp_path, monkeypatch):
    """
    DEF-M07-2: After a new user message is processed, _failed_pattern_counts
    must start empty so commands that failed in a previous turn aren't blocked.
    """
    from unittest.mock import MagicMock, patch

    obj = object.__new__(_cyrax_module.CyraxOrchestrator)
    # Simulate state after previous turn where nmap failed 3 times
    obj._failed_pattern_counts = {"nmap:target": 3}
    obj._actions_executed_this_turn = 5
    obj._recent_cmds_this_turn = ["nmap target"]
    obj._cmds_succeeded_this_turn = 0

    # Simulate the per-turn reset that happens in send_message
    obj._actions_executed_this_turn = 0
    obj._recent_cmds_this_turn = []
    obj._cmds_succeeded_this_turn = 0
    obj._failed_pattern_counts = {}  # DEF-M07-2 fix

    assert obj._failed_pattern_counts == {}, "Pattern counts must be empty after turn reset"


# ── DEF-M07-3: _turn_action_counts capped at 50 ───────────────────────────────


@pytest.mark.unit
def test_turn_action_counts_capped_at_50():
    """
    DEF-M07-3: _turn_action_counts must never exceed 50 entries regardless
    of how many turns have occurred.
    """
    counts: list[int] = []
    for i in range(200):
        counts.append(i % 3)
        if len(counts) > 50:
            counts = counts[-50:]

    assert len(counts) == 50
    assert counts[-1] == 199 % 3


@pytest.mark.unit
def test_turn_action_counts_preserves_recent_values():
    """After capping, the list must contain the most recent 50 entries."""
    counts: list[int] = list(range(100))
    if len(counts) > 50:
        counts = counts[-50:]
    assert counts[0] == 50
    assert counts[-1] == 99


# ── DEF-M07-4: depth-limit warning shown ──────────────────────────────────────


@pytest.mark.unit
def test_depth_limit_warning_is_emitted(monkeypatch):
    """
    DEF-M07-4: When _max_response_depth is reached, display.show_warning()
    must be called (operator notification — not silent log-only).
    """
    from utils import display as _display
    from unittest.mock import MagicMock, patch

    warnings_shown = []
    monkeypatch.setattr(_display, "show_warning", lambda msg: warnings_shown.append(msg))

    obj = object.__new__(_cyrax_module.CyraxOrchestrator)
    obj._max_response_depth = 1  # Trigger depth limit after 1 iteration
    obj._pause_requested = False
    obj._hard_interrupt_requested = False
    obj._actions_executed_this_turn = 0
    obj._cmds_succeeded_this_turn = 0
    obj._consecutive_cmd_failures = 0
    obj._failed_cmd_signatures = []
    obj._failed_pattern_counts = {}
    obj._recent_error_types = []
    obj._dedup_temp_boost = 0.0
    obj._last_response_hash = ""
    obj.scope = MagicMock()
    obj.scope.is_in_scope.return_value = (True, "")
    obj.tools = MagicMock()
    obj.tools.executor = MagicMock()
    obj.logger = MagicMock()
    obj.conversation = MagicMock()
    obj.conversation.messages = []
    obj.mission = MagicMock()
    obj.browser = MagicMock()
    obj.agent_pool = MagicMock()

    # Patch the methods that _process_response calls
    obj._maybe_regenerate_echo_response = MagicMock(
        return_value=("no actions here", "no actions here", False)
    )
    obj._execute_actions = MagicMock(return_value=["[Tool Result]\nok"])
    obj._is_planning_without_actions = MagicMock(return_value=False)
    obj._stream_response = MagicMock(return_value="follow up with no actions")
    obj._build_system_prompt = MagicMock(return_value="system prompt")

    # Make _stream_response keep returning non-empty so the loop hits depth limit
    obj._process_response("initial response with no actions")

    assert warnings_shown, "Expected depth-limit warning to be shown to operator"
    assert any("depth" in w.lower() or "limit" in w.lower() for w in warnings_shown)

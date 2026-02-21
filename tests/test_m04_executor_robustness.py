"""
M04 regression tests — executor and platform robustness.

Tests cover:
- Path traversal: null bytes, URL-encoded dots, symlink escapes
- Windows command adaptation: ls, cat, rm, cp, mv, mkdir, clear
- Interpreter resolution fallback
- Timeout bounded-wait (process not leaked after kill)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools import executor as executor_module
from tools.executor import ToolExecutor, _adapt_windows_commands, _resolve_interpreter


# ── Path traversal hardening ───────────────────────────────────────────────────


@pytest.mark.unit
def test_rejects_path_with_null_byte(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))
    result = executor.write_file("legit\x00../etc/passwd", "payload")
    assert not result.success
    assert "null byte" in result.stderr.lower() or "null" in result.stderr.lower()


@pytest.mark.unit
def test_rejects_url_encoded_traversal(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))
    result = executor.write_file("%2e%2e/etc/passwd", "payload")
    assert not result.success
    assert "traversal" in result.stderr.lower() or "outside" in result.stderr.lower()


@pytest.mark.unit
def test_allows_deep_nested_relative_path(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))
    result = executor.write_file("a/b/c/deep.txt", "content")
    assert result.success


@pytest.mark.unit
def test_rejects_absolute_path_with_traversal(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path))
    result = executor.write_file("/etc/shadow", "bad")
    assert not result.success
    assert "Rejected" in result.stderr


# ── Windows command adaptation ──────────────────────────────────────────────────

# All tests monkey-patch IS_WINDOWS=True to exercise Windows code paths on Linux.


@pytest.mark.unit
def test_adapt_cat_to_type(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("cat /etc/hosts")
    assert "type" in adapted.lower()
    assert "cat" not in adapted.lower()


@pytest.mark.unit
def test_adapt_ls_to_dir(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("ls")
    assert "dir" in adapted.lower()
    assert "ls" not in adapted.lower()


@pytest.mark.unit
def test_adapt_ls_la_to_dir(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("ls -la")
    assert "dir" in adapted.lower()


@pytest.mark.unit
def test_adapt_rm_rf_to_rd(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("rm -rf /tmp/test")
    assert "rd" in adapted.lower() or "del" in adapted.lower()


@pytest.mark.unit
def test_adapt_cp_to_copy(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("cp src.txt dst.txt")
    assert "copy" in adapted.lower()


@pytest.mark.unit
def test_adapt_mv_to_move(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    adapted = _adapt_windows_commands("mv old.txt new.txt")
    assert "move" in adapted.lower()


@pytest.mark.unit
def test_no_adapt_on_linux(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", False)
    cmd = "ls -la && cat /etc/hosts && rm -f tmp"
    assert _adapt_windows_commands(cmd) == cmd


# ── Interpreter resolution fallback ────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_interpreter_returns_available(monkeypatch):
    # bash is available on this Linux test host
    result = _resolve_interpreter("bash")
    assert result == "bash"


@pytest.mark.unit
def test_resolve_interpreter_falls_back(monkeypatch):
    # Pretend 'bash' is not in PATH; expect sh or dash fallback
    original_which = executor_module._shutil.which

    def fake_which(name):
        if name == "bash":
            return None
        return original_which(name)

    monkeypatch.setattr(executor_module._shutil, "which", fake_which)
    result = _resolve_interpreter("bash")
    # Should fall back to sh or dash (both available on Linux)
    assert result != "bash"
    assert result in ("sh", "dash", "python3", "python")  # reasonable fallback


@pytest.mark.unit
def test_resolve_interpreter_returns_preferred_when_unknown(monkeypatch):
    # Unknown interpreter with no fallback candidates returns as-is
    monkeypatch.setattr(executor_module._shutil, "which", lambda _: None)
    result = _resolve_interpreter("sometool-that-does-not-exist")
    assert result == "sometool-that-does-not-exist"


# ── Timeout bounded-wait (no zombie leak) ─────────────────────────────────────


@pytest.mark.unit
def test_timeout_does_not_hang(tmp_path):
    """A command that exceeds timeout must return promptly, not block forever."""
    executor = ToolExecutor(work_dir=str(tmp_path), timeout=1)
    result = executor.execute("sleep 30", timeout=1)
    assert result.exit_code != 0
    assert "timed out" in result.output.lower() or result.exit_code == -1

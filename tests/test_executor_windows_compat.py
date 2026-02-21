from tools import executor as executor_module


def test_adapt_windows_grep_pipe_basic(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    cmd = "curl -I https://example.com 2>&1 | grep ^HTTP"
    adapted = executor_module._adapt_windows_unix_filters(cmd)
    assert "findstr" in adapted
    assert "grep" not in adapted.lower()
    assert '/C:"^HTTP"' in adapted


def test_adapt_windows_grep_pipe_ignore_case(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", True)
    cmd = "curl -sIk https://example.com | grep -i 'server:'"
    adapted = executor_module._adapt_windows_unix_filters(cmd)
    assert "findstr" in adapted
    assert "/I" in adapted
    assert '/C:"server:"' in adapted


def test_adapt_windows_filter_noop_on_non_windows(monkeypatch):
    monkeypatch.setattr(executor_module, "IS_WINDOWS", False)
    cmd = "curl -I https://example.com 2>&1 | grep ^HTTP"
    adapted = executor_module._adapt_windows_unix_filters(cmd)
    assert adapted == cmd

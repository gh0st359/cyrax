from tools.executor import ToolExecutor
from utils.safety import ScopeEnforcer


def test_check_command_blocks_bash_c_wrapped_oos_url():
    scope = ScopeEnforcer(["in-scope.example.com"])
    allowed, reason = scope.check_command("bash -c 'curl http://evil.example.com'")

    assert not allowed
    assert "evil.example.com" in reason


def test_check_command_blocks_python_c_wrapped_oos_url():
    scope = ScopeEnforcer(["in-scope.example.com"])
    cmd = 'python -c "import requests; requests.get(\"http://evil.example.com\")"'
    allowed, reason = scope.check_command(cmd)

    assert not allowed
    assert "evil.example.com" in reason


def test_check_command_blocks_powershell_command_wrapped_oos_url():
    scope = ScopeEnforcer(["in-scope.example.com"])
    cmd = 'powershell -Command "Invoke-WebRequest http://evil.example.com"'
    allowed, reason = scope.check_command(cmd)

    assert not allowed
    assert "evil.example.com" in reason


def test_execute_script_blocks_oos_body_before_execution(tmp_path):
    scope = ScopeEnforcer(["in-scope.example.com"])
    executor = ToolExecutor(work_dir=str(tmp_path), scope_enforcer=scope)

    result = executor.execute_script('curl http://evil.example.com', interpreter='bash')

    assert not result.success
    assert result.exit_code == -1
    assert "evil.example.com" in result.stderr

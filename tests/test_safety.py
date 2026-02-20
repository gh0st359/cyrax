from utils.safety import ScopeEnforcer


def test_scope_enforcer_allows_configured_targets_and_localhost():
    scope = ScopeEnforcer(["example.com", "*.corp.local", "10.0.0.0/24", "192.168.1.10"])

    assert scope.is_in_scope("https://example.com/login")
    assert scope.is_in_scope("api.corp.local")
    assert scope.is_in_scope("10.0.0.42")
    assert scope.is_in_scope("192.168.1.10")
    assert scope.is_in_scope("http://localhost:8080")


def test_scope_enforcer_blocks_out_of_scope_command_targets():
    scope = ScopeEnforcer(["example.com"])

    allowed, reason = scope.check_command("curl https://evil.com")

    assert not allowed
    assert "NOT in your authorized scope" in reason
    assert "example.com" in reason


def test_scope_enforcer_allows_www_alias_for_apex_domain():
    scope = ScopeEnforcer(["kaidoagent.com"])

    assert scope.is_in_scope("https://www.kaidoagent.com/mission")

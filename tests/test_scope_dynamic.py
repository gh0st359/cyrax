"""Tests for dynamic scope switching, local path support, and refusal handling."""

import pytest

from utils.safety import ScopeEnforcer


# ─── Dynamic scope switching ──────────────────────────────────────

@pytest.mark.unit
def test_scope_reset_clears_and_reconfigures():
    scope = ScopeEnforcer(["example.com"])
    assert scope.is_in_scope("https://example.com")
    assert not scope.is_in_scope("https://other.com")

    scope.reset(["other.com"])
    assert scope.is_in_scope("https://other.com")
    assert not scope.is_in_scope("https://example.com")


@pytest.mark.unit
def test_scope_add_targets_extends_without_removing():
    scope = ScopeEnforcer(["example.com"])
    scope.add_targets(["other.com"])

    assert scope.is_in_scope("https://example.com")
    assert scope.is_in_scope("https://other.com")


@pytest.mark.unit
def test_scope_reset_to_empty_disables():
    scope = ScopeEnforcer(["example.com"])
    assert scope.enabled

    scope.reset()
    assert not scope.enabled
    # Everything should be allowed when disabled
    assert scope.is_in_scope("https://anything.com")


# ─── Local filesystem path support ────────────────────────────────

@pytest.mark.unit
def test_scope_local_path_target():
    scope = ScopeEnforcer(["/Users/henry/Downloads/cyrax"])
    assert scope.enabled
    assert scope.is_path_in_scope("/Users/henry/Downloads/cyrax")
    assert scope.is_path_in_scope("/Users/henry/Downloads/cyrax/src/main.py")
    assert not scope.is_path_in_scope("/Users/henry/Documents/other")


@pytest.mark.unit
def test_scope_local_path_tilde():
    scope = ScopeEnforcer(["~/projects/myapp"])
    assert scope.enabled
    assert len(scope.allowed_paths) == 1


@pytest.mark.unit
def test_scope_mixed_domain_and_path():
    scope = ScopeEnforcer(["example.com", "/tmp/test-project"])
    assert scope.is_in_scope("https://example.com")
    assert scope.is_path_in_scope("/tmp/test-project/file.py")
    assert not scope.is_path_in_scope("/home/user/other")


@pytest.mark.unit
def test_scope_local_path_does_not_block_domains():
    scope = ScopeEnforcer(["/Users/henry/project"])
    # Domain checks should not be affected by path-only scope
    # When only paths are in scope, domains are still checked normally
    allowed, _ = scope.check_command("ls /Users/henry/project")
    assert allowed


@pytest.mark.unit
def test_add_dir_local_path():
    scope = ScopeEnforcer(["example.com"])
    scope.add_targets(["/tmp/local-project"])
    assert scope.is_in_scope("https://example.com")
    assert scope.is_path_in_scope("/tmp/local-project/src")


# ─── Scope description ──────────────────────────────────────

@pytest.mark.unit
def test_scope_description_includes_paths():
    scope = ScopeEnforcer(["/Users/henry/project", "example.com"])
    desc = scope.get_scope_description()
    assert "/Users/henry/project" in desc
    assert "example.com" in desc


@pytest.mark.unit
def test_scope_add_targets_no_duplicates():
    scope = ScopeEnforcer(["example.com"])
    scope.add_targets(["example.com"])
    # Should not duplicate
    assert scope._raw_targets.count("example.com") == 1


# ─── Action parsing: READ_FILE ───────────────────────────────

@pytest.mark.unit
def test_read_file_action_parsed():
    from cyrax import _find_all_actions
    response = 'Reading the config.\n[READ_FILE path="config.yaml"]\n'
    actions = _find_all_actions(response)
    assert len(actions) == 1
    assert actions[0][1] == "read_file"


@pytest.mark.unit
def test_read_file_and_execute_actions_ordered():
    from cyrax import _find_all_actions
    response = (
        '[READ_FILE path="src/main.py"]\n'
        'Now running tests.\n'
        '[EXECUTE] pytest [/EXECUTE]\n'
    )
    actions = _find_all_actions(response)
    assert len(actions) == 2
    assert actions[0][1] == "read_file"
    assert actions[1][1] == "execute"

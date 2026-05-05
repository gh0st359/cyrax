"""
M11 regression tests — productization and operator experience.

Tests cover:
- pyproject.toml has all required metadata fields
- Version string is PEP-440 MAJOR.MINOR.PATCH
- release_check detects missing metadata
- release_check detects invalid version format
- release_check reports gate reports existence
- run_release_check returns structured (results, bool) tuple
- CLI entry point 'cyrax:main' is importable
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from release_check import (  # noqa: E402
    check_pyproject_metadata,
    check_version_format,
    run_release_check,
)


# ── pyproject.toml metadata ───────────────────────────────────────────────────

_REQUIRED_METADATA = [
    "name", "version", "description", "authors",
    "keywords", "classifiers", "requires-python", "license",
]


@pytest.mark.unit
def test_pyproject_has_all_required_fields():
    """pyproject.toml must contain all required metadata keys."""
    name, passed, detail = check_pyproject_metadata()
    assert passed, f"pyproject metadata check failed: {detail}"


@pytest.mark.unit
def test_pyproject_version_is_pep440():
    """Version must match MAJOR.MINOR.PATCH format."""
    name, passed, detail = check_version_format()
    assert passed, f"version check failed: {detail}"


@pytest.mark.unit
def test_pyproject_version_value():
    """Version value should be 1.0.0 or higher."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    assert m, "version field not found in pyproject.toml"
    parts = [int(x) for x in m.group(1).split(".")]
    assert parts[0] >= 1, f"Major version must be >= 1, got {m.group(1)}"


@pytest.mark.unit
def test_pyproject_has_authors():
    pyproject = _REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "authors" in text


@pytest.mark.unit
def test_pyproject_has_classifiers():
    pyproject = _REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "classifiers" in text
    assert "Security" in text or "security" in text


@pytest.mark.unit
def test_pyproject_has_project_urls():
    pyproject = _REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "[project.urls]" in text


# ── release_check detection logic ────────────────────────────────────────────


@pytest.mark.unit
def test_check_metadata_fails_with_missing_field(tmp_path, monkeypatch):
    """check_pyproject_metadata must fail when a required field is absent."""
    # Write a minimal pyproject without 'authors'
    fake = tmp_path / "pyproject.toml"
    fake.write_text('[project]\nname = "cyrax"\nversion = "1.0.0"\n')

    import release_check as rc
    monkeypatch.setattr(rc, "_REPO_ROOT", tmp_path)
    name, passed, detail = rc.check_pyproject_metadata()
    assert not passed
    assert "authors" in detail


@pytest.mark.unit
def test_check_version_fails_for_invalid_format(tmp_path, monkeypatch):
    """check_version_format must fail when version is not MAJOR.MINOR.PATCH."""
    fake = tmp_path / "pyproject.toml"
    fake.write_text('[project]\nversion = "1.0.0-beta"\n')

    import release_check as rc
    monkeypatch.setattr(rc, "_REPO_ROOT", tmp_path)
    name, passed, detail = rc.check_version_format()
    assert not passed
    assert "1.0.0-beta" in detail


@pytest.mark.unit
def test_check_gate_reports_fails_when_file_missing(tmp_path, monkeypatch):
    """check_gate_reports must fail when a gate JSON is absent."""
    # Create some but not all gates
    gates = tmp_path / "reports" / "gates"
    gates.mkdir(parents=True)
    (gates / "M00.json").write_text("{}")

    import release_check as rc
    monkeypatch.setattr(rc, "_REPO_ROOT", tmp_path)
    name, passed, detail = rc.check_gate_reports(up_to="M01")
    assert not passed
    assert "M01.json" in detail


@pytest.mark.unit
def test_check_gate_reports_passes_when_all_present(tmp_path, monkeypatch):
    """check_gate_reports must pass when all gate JSONs exist."""
    gates = tmp_path / "reports" / "gates"
    gates.mkdir(parents=True)
    for i in range(3):  # M00 M01 M02
        (gates / f"M{i:02d}.json").write_text("{}")

    import release_check as rc
    monkeypatch.setattr(rc, "_REPO_ROOT", tmp_path)
    name, passed, detail = rc.check_gate_reports(up_to="M02")
    assert passed


@pytest.mark.unit
def test_check_milestones_fails_for_pending_milestone(tmp_path, monkeypatch):
    """check_milestones must fail if any milestone through up_to is not 'done'."""
    tracker = tmp_path / "reports" / "MILESTONE_TRACKER.md"
    tracker.parent.mkdir(parents=True)
    tracker.write_text(
        "| M00 | Control Plane | done | ... |\n"
        "| M01 | Environment | pending | — |\n",
        encoding="utf-8",
    )

    import release_check as rc
    monkeypatch.setattr(rc, "_REPO_ROOT", tmp_path)
    name, passed, detail = rc.check_milestones(up_to="M01")
    assert not passed
    assert "M01" in detail


@pytest.mark.unit
def test_run_release_check_returns_tuple():
    """run_release_check must return (list[CheckResult], bool)."""
    results, all_passed = run_release_check(skip_tests=True, up_to="M10")
    assert isinstance(results, list)
    assert isinstance(all_passed, bool)
    for item in results:
        name, passed, detail = item
        assert isinstance(name, str)
        assert isinstance(passed, bool)
        assert isinstance(detail, str)


@pytest.mark.unit
def test_run_release_check_up_to_m10_passes():
    """Release check through M10 must pass (with tests skipped)."""
    results, all_passed = run_release_check(skip_tests=True, up_to="M10")
    failed = [(n, d) for n, p, d in results if not p and n != "git_clean"]
    assert not failed, f"Non-git checks failed: {failed}"


# ── CLI entry point ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cyrax_main_is_importable():
    """cyrax:main must be importable (used as CLI entry point in pyproject.toml)."""
    import cyrax
    assert hasattr(cyrax, "main"), "cyrax module must expose a 'main' function"
    assert callable(cyrax.main)


@pytest.mark.unit
def test_cli_parser_exposes_subcommands():
    """CYRAX CLI should expose setup/status/tooling subcommands."""
    import cyrax

    parser = cyrax.create_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"
    assert callable(args.handler)

    args = parser.parse_args(["configure", "--provider", "ollama", "--model", "llama3.1"])
    assert args.command == "configure"
    assert args.provider == "ollama"
    assert args.model == "llama3.1"

    bare = parser.parse_args([])
    assert bare.command is None
    assert callable(bare.handler)


@pytest.mark.unit
def test_grok_environment_selects_xai_provider(monkeypatch):
    """Bare `cyrax` should work with the user's GROK_* environment variables."""
    import cyrax

    monkeypatch.setenv("GROK_API_KEY", "xai-test")
    monkeypatch.setenv("GROK_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("GROK_PRIMARY_MODEL", "grok-4.3")

    config = cyrax.load_config("/tmp/definitely-missing-cyrax-config.yaml")
    assert config["model"]["provider"] == "xai"
    assert config["model"]["api_key"] == "xai-test"
    assert config["model"]["api_url"] == "https://api.x.ai/v1"
    assert config["model"]["model_name"] == "grok-4.3"


@pytest.mark.unit
def test_load_config_merges_defaults_and_redacts_keys(tmp_path):
    """Partial configs should inherit defaults and redact secrets for display."""
    import cyrax

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "model:\n"
        "  provider: openai\n"
        "  api_key: sk-test-secret\n",
        encoding="utf-8",
    )

    config = cyrax.load_config(str(config_file))
    assert config["model"]["model_name"] == "gpt-4o"
    assert config["tools"]["timeout"] == 300

    redacted = cyrax._redact_config(config)
    assert redacted["model"]["api_key"] == "sk-t...cret"

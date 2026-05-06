"""Tests for CYRAX autonomy upgrades."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daemon.heartbeat import HeartbeatMonitor
from recovery.engine import RecoveryEngine
from skills.manager import SkillManager
from tools.bootstrap import ToolBootstrapper
from tools.executor import ToolExecutor


@pytest.mark.unit
def test_skill_manager_discovers_project_skill(tmp_path):
    skill_dir = tmp_path / ".cyrax" / "skills" / "repo-audit"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: repo-audit\n"
        "description: Audit local repositories\n"
        "trigger: repository\n"
        "tools: grep, python\n"
        "---\n\n"
        "Inspect dependency manifests and source files.",
        encoding="utf-8",
    )

    manager = SkillManager(project_root=str(tmp_path))

    skills = manager.list_skills()
    assert len(skills) == 1
    assert skills[0]["name"] == "repo-audit"
    assert (
        manager.get_skill("repo-audit").description
        == "Audit local repositories"
    )
    assert "Inspect dependency manifests" in manager.invoke_skill("repo-audit")


@pytest.mark.unit
def test_skill_manager_matches_trigger(tmp_path):
    skill_dir = tmp_path / ".cyrax" / "skills" / "web"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: web\n"
        "description: Web app checks\n"
        "trigger: auth\n"
        "---\n\n"
        "Check forms.",
        encoding="utf-8",
    )

    manager = SkillManager(project_root=str(tmp_path))

    matched = manager.match_skills_for_context("audit the auth flow")
    assert [skill.name for skill in matched] == ["web"]


@pytest.mark.unit
def test_tool_bootstrap_guidance_for_missing_tool(tmp_path):
    bootstrapper = ToolBootstrapper(auto_install=False, work_dir=tmp_path)

    result = bootstrapper.bootstrap("nmap", reason="scan requested")

    if not bootstrapper.is_available("nmap"):
        assert not result.success
        assert not result.attempted
        assert (
            "sudo apt-get" in result.guidance
            or "brew install" in result.guidance
        )


@pytest.mark.unit
def test_executor_reports_bootstrap_guidance_for_missing_command(tmp_path):
    executor = ToolExecutor(work_dir=str(tmp_path), auto_install_tools=False)
    missing = "cyraxdefinitelymissingtool"

    result = executor.execute(f"{missing} --version")

    assert result.exit_code == 127
    assert f"Missing tool: {missing}" in result.stderr
    assert "script the capability directly" in result.stderr


@pytest.mark.unit
def test_heartbeat_persists_status(tmp_path):
    state_path = tmp_path / "HEARTBEAT.json"
    monitor = HeartbeatMonitor(
        interval_seconds=5,
        state_path=state_path,
        enabled=False,
    )

    monitor.tick("testing", active_agents=2)
    status = monitor.status()

    assert status.status == "testing"
    assert status.active_agents == 2
    assert state_path.exists()
    assert "testing" in state_path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_recovery_engine_produces_concrete_alternatives():
    engine = RecoveryEngine()

    guidance = engine.guidance_for("bash: nmap: command not found")

    assert "AUTONOMOUS RECOVERY REQUIRED" in guidance
    assert "Bootstrap missing capability" in guidance
    assert "[EXECUTE]" in guidance


@pytest.mark.unit
def test_orchestrator_commands_include_autonomy(monkeypatch, tmp_path):
    import cyrax as _c

    obj = object.__new__(_c.CyraxOrchestrator)
    obj.skills = SkillManager(project_root=str(tmp_path))
    obj.heartbeat = HeartbeatMonitor(
        state_path=tmp_path / "HEARTBEAT.json",
        enabled=False,
    )
    obj.gateway = MagicMock()
    obj.gateway.state.return_value = {"events": 3, "agents": {}}
    obj.model = MagicMock()
    obj.model.provider = "test"
    obj.model.model_name = "dummy"
    obj.model.temperature = 0
    obj.model.max_tokens = 1
    obj.tools = MagicMock()
    obj.tools.executor = MagicMock()
    obj.tools.executor.work_dir = Path(tmp_path)
    obj._session_scope_label = lambda: "none"
    obj._current_mode_label = lambda: "interactive"
    obj.campaign = MagicMock()
    obj.campaign.to_dict.return_value = {}
    obj._queued_user_message = None
    obj._active_skill_context = ""

    with monkeypatch.context() as m:
        m.setattr(_c.display, "show_info", lambda *_args, **_kwargs: None)
        m.setattr(_c.display, "show_success", lambda *_args, **_kwargs: None)
        m.setattr(
            _c.display,
            "show_campaign_status",
            lambda *_args, **_kwargs: None,
        )
        assert obj.handle_command("/skill list") == ""
        assert obj.handle_command("/heartbeat") == ""
        assert obj.handle_command("/daemon") == ""

import pytest

import cyrax
from tools.executor import CommandResult


class DummyModelManager:
    def __init__(self, _config):
        self.provider = "test"
        self.model_name = "dummy"
        self.temperature = 0.0
        self.max_tokens = 256
        self._responses = [
            "I will run a quick check. [EXECUTE]echo integration-pass[/EXECUTE]",
            "Done. Command completed.",
        ]

    def generate_stream(self, **_kwargs):
        response = self._responses.pop(0)
        yield {"delta": response}
        yield {"done": True, "tokens_in": 1, "tokens_out": 1}


class DummyAgentPool:
    def __init__(self, *args, **kwargs):
        pass

    def shutdown(self):
        return None


@pytest.mark.integration
def test_orchestrator_one_turn_executes_action_and_processes_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(cyrax, "ModelManager", DummyModelManager)
    monkeypatch.setattr(cyrax, "SubprocessAgentPool", DummyAgentPool)

    config = {
        "model": {"provider": "openai", "model_name": "dummy", "api_key": "x"},
        "tools": {"work_dir": str(tmp_path), "timeout": 5, "allow_dangerous": False},
        "safety": {"auto_approve": True},
        "memory": {"db_path": str(tmp_path / "cyrax.db")},
        "logging": {"log_dir": str(tmp_path / "logs"), "level": "INFO"},
        "display": {"show_reasoning": False},
    }

    orchestrator = cyrax.CyraxOrchestrator(config)

    executed = []

    def fake_execute(command, timeout=None, cwd=None, env=None):
        executed.append(command)
        return CommandResult(command, "integration-pass", "", 0)

    monkeypatch.setattr(orchestrator.tools.executor, "execute", fake_execute)

    response = orchestrator.chat("Test target is example.com")

    assert "Done. Command completed." in response
    assert executed == ["echo integration-pass"]
    assert any(
        msg["role"] == "user" and "[Action Results]" in msg["content"]
        for msg in orchestrator.conversation.get_messages()
    )

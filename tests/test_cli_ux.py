from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

import cyrax
from memory.conversation import ConversationMemory
from utils import display


def test_help_command_renders_new_command_menu(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(display, "console", Console(file=output, force_terminal=False, width=120))

    obj = object.__new__(cyrax.CyraxOrchestrator)
    obj._campaign_mode = False
    obj._slash_commands = cyrax.CyraxOrchestrator._slash_commands.__get__(obj)

    result = cyrax.CyraxOrchestrator.handle_command(obj, "/help")

    rendered = output.getvalue()
    assert result == ""
    assert "CYRAX Commands" in rendered
    assert "/compact" in rendered
    assert "/model" in rendered
    assert "/mode" in rendered


def test_model_and_mode_commands_update_runtime(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(display, "console", Console(file=output, force_terminal=False, width=120))

    obj = object.__new__(cyrax.CyraxOrchestrator)
    obj.model = MagicMock()
    obj.model.provider = "anthropic"
    obj.model.model_name = "old-model"
    obj.model.client = MagicMock()
    obj.permission_gate = MagicMock()
    obj.permission_gate.auto_approve = False
    obj.permission_gate.policy_mode = "interactive"
    obj._current_mode_label = cyrax.CyraxOrchestrator._current_mode_label.__get__(obj)

    assert cyrax.CyraxOrchestrator.handle_command(obj, "/model new-model") == ""
    assert obj.model.model_name == "new-model"
    assert obj.model.client.model == "new-model"

    assert cyrax.CyraxOrchestrator.handle_command(obj, "/mode auto") == ""
    obj.permission_gate.auto_approve_all.assert_called_once()


def test_compact_command_summarizes_conversation(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(display, "console", Console(file=output, force_terminal=False, width=120))

    obj = object.__new__(cyrax.CyraxOrchestrator)
    obj.conversation = ConversationMemory(max_history=50)
    for i in range(8):
        obj.conversation.add_message("user", f"message {i}")

    assert cyrax.CyraxOrchestrator.handle_command(obj, "/compact 3") == ""

    rendered = output.getvalue()
    assert "compact" in rendered.lower()
    assert len(obj.conversation.messages) == 3


def test_parser_supports_prompt_argument_and_print_flag():
    parser = cyrax.create_parser()
    args = parser.parse_args(["chat", "scan example.com", "--print", "--auto"])
    assert args.prompt == "scan example.com"
    assert args.print_response is True
    assert args.auto is True

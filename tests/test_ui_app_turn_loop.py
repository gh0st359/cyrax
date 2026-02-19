import sys
import unittest

from ui import app as app_module


class _FakeOutput:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)


class _FakeStatus:
    def __init__(self):
        self.states = []

    def update(self, message):
        self.states.append(message)


class _FakeCampaign:
    status = "active"


class _FakeOrchestrator:
    def __init__(self, max_turns=250, queue_inject_turn=None):
        self._campaign_mode = True
        self.campaign = _FakeCampaign()
        self._consecutive_empty_turns = 0
        self._actions_executed_this_turn = 1
        self._turn_action_counts = []
        self._queued_user_message = None
        self._saved = 0
        self.max_turns = max_turns
        self.queue_inject_turn = queue_inject_turn
        self.inputs = []
        self.max_stack_depth = 0

    def chat(self, user_input):
        self.inputs.append(user_input)

        depth = 0
        frame = sys._getframe()
        while frame:
            depth += 1
            frame = frame.f_back
        self.max_stack_depth = max(self.max_stack_depth, depth)

        if self.queue_inject_turn and len(self.inputs) == self.queue_inject_turn:
            self._queued_user_message = "PRIORITIZED USER INPUT"

        if len(self.inputs) >= self.max_turns:
            self.campaign.status = "paused"

    def _save_campaign_state(self):
        self._saved += 1


class _FakeApp:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self._turn_count = 0
        self._ai_running = False
        self.output = _FakeOutput()
        self.status = _FakeStatus()

    def query_one(self, selector, _kind):
        if selector == "#output":
            return self.output
        if selector == "#status-bar":
            return self.status
        raise AssertionError(f"unexpected selector: {selector}")

    def call_from_thread(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


@unittest.skipUnless(
    hasattr(app_module, "CyraxApp") and hasattr(app_module.CyraxApp, "_run_ai_turn_loop"),
    "Textual app implementation not available",
)
class RunAiTurnLoopTests(unittest.TestCase):
    def test_queued_input_overrides_continue(self):
        orchestrator = _FakeOrchestrator(max_turns=8, queue_inject_turn=3)
        app = _FakeApp(orchestrator)

        app_module.CyraxApp._run_ai_turn_loop(app, "initial user input")

        self.assertIn("PRIORITIZED USER INPUT", orchestrator.inputs)
        prioritized_index = orchestrator.inputs.index("PRIORITIZED USER INPUT")
        self.assertEqual(orchestrator.inputs[prioritized_index - 1], "Continue.")

    def test_long_run_smoke_no_recursion_growth(self):
        orchestrator = _FakeOrchestrator(max_turns=400)
        app = _FakeApp(orchestrator)

        app_module.CyraxApp._run_ai_turn_loop(app, "start")

        self.assertEqual(len(orchestrator.inputs), 400)
        self.assertEqual(app._turn_count, 400)
        self.assertFalse(app._ai_running)
        self.assertGreaterEqual(orchestrator._saved, 1)

        # In iterative mode this remains bounded and does not increase with turn count.
        self.assertLess(orchestrator.max_stack_depth, 80)


if __name__ == "__main__":
    unittest.main()

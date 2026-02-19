import unittest
from types import SimpleNamespace

from cyrax import CyraxOrchestrator


class EchoGuardTests(unittest.TestCase):
    def _build_orchestrator(self):
        orch = CyraxOrchestrator.__new__(CyraxOrchestrator)
        orch.conversation = SimpleNamespace(messages=[])
        orch.logger = SimpleNamespace(
            log_event=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            log_conversation=lambda *args, **kwargs: None,
            log_error=lambda *args, **kwargs: None,
        )
        orch._build_system_prompt = lambda: "system"
        orch._stream_response = lambda _prompt: "fresh response"
        orch.conversation.add_message = lambda role, content: orch.conversation.messages.append(
            {"role": role, "content": content}
        )
        return orch

    def test_detect_user_echo_overlap_for_highly_similar_text(self):
        orch = self._build_orchestrator()
        user = "scan the target and report open ports and versions"
        echoed = "Scan the target and report open ports and versions."

        overlap = orch._detect_user_echo_overlap(echoed, user)

        self.assertIsNotNone(overlap)
        self.assertGreaterEqual(overlap["token_overlap"], 0.8)

    def test_detect_user_echo_overlap_ignores_different_text(self):
        orch = self._build_orchestrator()
        user = "scan the target and report open ports and versions"
        response = "I'll start with nmap service detection then pivot to web endpoints."

        overlap = orch._detect_user_echo_overlap(response, user)

        self.assertIsNone(overlap)

    def test_regenerates_and_injects_internal_feedback_on_echo_without_actions(self):
        orch = self._build_orchestrator()
        orch.conversation.messages = [
            {"role": "user", "content": "Enumerate the target and check auth bypasses"},
            {"role": "assistant", "content": "working"},
        ]

        response, accumulated, regenerated = orch._maybe_regenerate_echo_response(
            response="Enumerate the target and check auth bypasses",
            accumulated="Enumerate the target and check auth bypasses",
            depth=0,
            echo_regens=0,
        )

        self.assertTrue(regenerated)
        self.assertEqual(response, "fresh response")
        self.assertIn("[Internal Feedback]", orch.conversation.messages[-2]["content"])
        self.assertEqual(orch.conversation.messages[-1]["content"], "fresh response")
        self.assertIn("fresh response", accumulated)

    def test_no_regeneration_when_action_block_present(self):
        orch = self._build_orchestrator()
        orch.conversation.messages = [
            {"role": "user", "content": "run a quick recon"},
        ]

        response, accumulated, regenerated = orch._maybe_regenerate_echo_response(
            response="[EXECUTE] nmap -sV 10.0.0.5 [/EXECUTE]",
            accumulated="[EXECUTE] nmap -sV 10.0.0.5 [/EXECUTE]",
            depth=0,
            echo_regens=0,
        )

        self.assertFalse(regenerated)
        self.assertEqual(response, "[EXECUTE] nmap -sV 10.0.0.5 [/EXECUTE]")
        self.assertEqual(accumulated, "[EXECUTE] nmap -sV 10.0.0.5 [/EXECUTE]")


if __name__ == "__main__":
    unittest.main()

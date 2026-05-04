"""
CYRAX Conversation Memory
Manages conversation history with smart summarization-based eviction.
"""

import json
import re
import copy
from typing import Optional
from datetime import datetime, timezone


class ConversationMemory:
    """
    Manages conversation history for CYRAX and its agents.
    Uses fact extraction to summarize older messages instead of
    just truncating them — preserving key findings, commands, and errors.

    Optionally linked to a MissionMemory instance for persistent fact storage.
    When messages are evicted, key facts are pushed to mission memory
    in addition to the running summary, ensuring they survive indefinitely.
    """

    def __init__(self, max_history: int = 50, mission_memory=None):
        self.messages: list[dict] = []
        self.max_history = max_history
        self.total_messages = 0
        self.summary = ""  # Running summary of evicted messages
        self.mission_memory = mission_memory  # Optional MissionMemory reference

    def add_message(self, role: str, content: str, metadata: Optional[dict] = None):
        """Add a message to the conversation."""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self.messages.append(message)
        self.total_messages += 1

        if len(self.messages) > self.max_history:
            self._trim_history()

    def _trim_history(self):
        """Trim history using fact extraction instead of raw truncation."""
        if len(self.messages) <= self.max_history:
            return

        # Summarize the oldest 10 messages
        cutoff = 10
        old_messages = self.messages[:cutoff]

        # Extract structured facts from old messages
        facts = self._extract_facts(old_messages)
        if facts:
            self.summary += f"\n{facts}" if self.summary else facts

        # Push extracted facts to mission memory for permanent storage
        if self.mission_memory and facts:
            for line in facts.split("\n"):
                line = line.strip().lstrip("- ")
                if line and line != "Session history:":
                    self.mission_memory.add_session_fact(line)

        # Keep the summary message + recent messages
        self.messages = self.messages[cutoff:]

    def _extract_facts(self, messages: list[dict]) -> str:
        """Extract key facts from messages — commands, findings, errors."""
        facts = []
        for msg in messages:
            content = msg.get("content", "")

            # Extract commands that were executed
            for match in re.finditer(r'\[EXECUTE\]\s*(.*?)\s*\[/EXECUTE\]', content, re.DOTALL):
                cmd = match.group(1).strip()[:120]
                facts.append(f"- Ran: {cmd}")

            # Extract file writes
            for match in re.finditer(r'\[WRITE_FILE\s+path="([^"]+)"', content):
                facts.append(f"- Wrote: {match.group(1)}")

            # Extract findings
            for match in re.finditer(r'\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"', content):
                facts.append(f"- Finding [{match.group(1)}]: {match.group(2)}")

            # Extract agent spawns
            for match in re.finditer(r'\[SPAWN\s+type="(\w+)"\](.*?)\[/SPAWN\]', content, re.DOTALL):
                facts.append(f"- Spawned {match.group(1)} agent: {match.group(2).strip()[:80]}")

            # Extract key tool results (errors, scope violations)
            if "[Scope Violation]" in content:
                facts.append("- Scope violation blocked an action")
            if "[Permission Denied]" in content:
                facts.append("- User denied a permission request")

            # Extract notable errors
            if "failed" in content.lower() and msg["role"] == "user":
                # Extract the command that failed from tool results
                cmd_match = re.search(r'\[Tool Result for: (.*?)\]', content)
                if cmd_match:
                    facts.append(f"- Failed: {cmd_match.group(1)[:80]}")

        if not facts:
            return ""

        # Deduplicate
        seen = set()
        unique_facts = []
        for f in facts:
            if f not in seen:
                seen.add(f)
                unique_facts.append(f)

        return "Session history:\n" + "\n".join(unique_facts[-20:])  # Keep last 20 facts

    def get_messages(self) -> list[dict]:
        """Get messages formatted for the model API, with summary context."""
        result = []
        if self.summary:
            result.append({
                "role": "user",
                "content": f"[Previous session context]\n{self.summary}"
            })
        for m in self.messages:
            result.append({"role": m["role"], "content": m["content"]})
        return result

    def get_last_n(self, n: int) -> list[dict]:
        """Get the last N messages."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages[-n:]
        ]

    def get_full_history(self) -> list[dict]:
        """Get full message history with metadata."""
        return copy.deepcopy(self.messages)

    def clear(self):
        """Clear conversation history."""
        self.messages.clear()
        self.total_messages = 0
        self.summary = ""

    def to_json(self) -> str:
        """Serialize conversation to JSON."""
        return json.dumps(
            {
                "messages": self.messages,
                "total_messages": self.total_messages,
                "max_history": self.max_history,
                "summary": self.summary,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "ConversationMemory":
        """Deserialize conversation from JSON."""
        parsed = json.loads(data)
        memory = cls(max_history=parsed.get("max_history", 50))
        memory.messages = parsed.get("messages", [])
        memory.total_messages = parsed.get("total_messages", 0)
        memory.summary = parsed.get("summary", "")
        return memory

    def __len__(self) -> int:
        return len(self.messages)

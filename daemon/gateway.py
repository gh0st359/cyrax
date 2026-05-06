"""
CYRAX Gateway

In-process gateway that centralizes runtime events and agent state.
It provides OpenClaw-style coordination without a heavy server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class GatewayEvent:
    type: str
    payload: dict[str, Any]
    timestamp: str


class Gateway:
    """Runtime event bus and state persistence layer."""

    def __init__(self, state_dir: str | Path = ".cyrax/runtime"):
        self.state_dir = Path(state_dir)
        self.events_path = self.state_dir / "events.jsonl"
        self.state_path = self.state_dir / "state.json"
        self._state: dict[str, Any] = {
            "events": 0,
            "agents": {},
            "last_event": None,
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, payload: dict[str, Any]) -> GatewayEvent:
        event = GatewayEvent(
            type=event_type,
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._state["events"] = int(self._state.get("events", 0)) + 1
        self._state["last_event"] = asdict(event)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(event)) + "\n")
        self._persist()
        return event

    def update_agent(self, agent_id: str, status: dict[str, Any]) -> None:
        agents = self._state.setdefault("agents", {})
        agents[agent_id] = status
        self.emit("agent_status", {"agent_id": agent_id, **status})

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def _persist(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, indent=2),
            encoding="utf-8",
        )

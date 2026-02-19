"""
CYRAX Campaign State
Tracks the current state of the red team engagement.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict


@dataclass
class AccessLevel:
    """Represents an access level achieved on a target."""

    target: str
    user: str
    level: str  # "user", "admin", "system", "domain_admin"
    method: str  # How access was obtained
    timestamp: str = ""
    active: bool = True

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class AttackPathStep:
    """A step in an attack path."""

    stage: str
    target: str
    technique: str
    result: str
    agent_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class CampaignState:
    """
    Tracks the overall state of the engagement campaign.
    Maintains objectives, access levels, attack paths, and agent status.
    """

    def __init__(self):
        self.name: str = ""
        self.target: str = ""
        self.objective: str = ""
        self.start_time: str = datetime.now(timezone.utc).isoformat()
        self.status: str = "initialized"  # initialized, active, paused, completed

        # Access and compromise tracking
        self.access_levels: list[AccessLevel] = []
        self.attack_path: list[AttackPathStep] = []
        self.compromised_hosts: list[str] = []
        self.discovered_networks: list[str] = []

        # Agent tracking
        self.active_agents: dict[str, dict] = {}

        # Metadata
        self.notes: list[str] = []

    def set_target(self, target: str, objective: str = ""):
        """Set the engagement target and objective."""
        self.target = target
        self.objective = objective
        self.status = "active"

    def add_access(
        self,
        target: str,
        user: str,
        level: str,
        method: str,
    ):
        """Record a new access level achieved."""
        access = AccessLevel(target=target, user=user, level=level, method=method)
        self.access_levels.append(access)
        if target not in self.compromised_hosts:
            self.compromised_hosts.append(target)

    def add_attack_step(
        self,
        stage: str,
        target: str,
        technique: str,
        result: str,
        agent_id: str = "",
    ):
        """Add a step to the attack path."""
        step = AttackPathStep(
            stage=stage,
            target=target,
            technique=technique,
            result=result,
            agent_id=agent_id,
        )
        self.attack_path.append(step)

    def register_agent(self, agent_id: str, agent_type: str, task: str,
                       pid: int = 0, socket_path: str = ""):
        """Register an active agent with optional process tracking."""
        self.active_agents[agent_id] = {
            "type": agent_type,
            "task": task,
            "status": "active",
            "started": datetime.now(timezone.utc).isoformat(),
            "pid": pid,
            "socket_path": socket_path,
        }

    def update_agent_status(self, agent_id: str, status: str):
        """Update an agent's status."""
        if agent_id in self.active_agents:
            self.active_agents[agent_id]["status"] = status

    def get_orphaned_agents(self) -> list[dict]:
        """Find agents that were running when the orchestrator last exited."""
        orphaned = []
        for agent_id, info in self.active_agents.items():
            if info["status"] in ("active", "orphaned") and info.get("pid"):
                orphaned.append({"agent_id": agent_id, **info})
        return orphaned

    def mark_agents_orphaned(self):
        """Mark all active agents as orphaned (called before orchestrator exit)."""
        for agent_id, info in self.active_agents.items():
            if info["status"] == "active":
                info["status"] = "orphaned"

    def add_note(self, note: str):
        """Add an engagement note."""
        self.notes.append(
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {note}"
        )

    def summary(self) -> str:
        """Get a formatted summary for use in system prompts."""
        lines = [
            f"Target: {self.target or 'Not set'}",
            f"Objective: {self.objective or 'Not set'}",
            f"Status: {self.status}",
        ]

        if self.compromised_hosts:
            lines.append(f"Compromised hosts: {', '.join(self.compromised_hosts)}")

        if self.access_levels:
            lines.append("Access levels:")
            for access in self.access_levels:
                lines.append(
                    f"  - {access.user}@{access.target} [{access.level}] via {access.method}"
                )

        if self.active_agents:
            active = [
                f"{aid} ({info['type']})"
                for aid, info in self.active_agents.items()
                if info["status"] == "active"
            ]
            if active:
                lines.append(f"Active agents: {', '.join(active)}")

        if self.attack_path:
            lines.append("Attack path:")
            for i, step in enumerate(self.attack_path, 1):
                lines.append(
                    f"  {i}. [{step.stage}] {step.technique} -> {step.result}"
                )

        if self.discovered_networks:
            lines.append(f"Networks: {', '.join(self.discovered_networks)}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize campaign state to dict."""
        return {
            "name": self.name,
            "target": self.target,
            "objective": self.objective,
            "start_time": self.start_time,
            "status": self.status,
            "access_levels": [asdict(a) for a in self.access_levels],
            "attack_path": [asdict(s) for s in self.attack_path],
            "compromised_hosts": self.compromised_hosts,
            "discovered_networks": self.discovered_networks,
            "active_agents": self.active_agents,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignState":
        """Deserialize from dict."""
        state = cls()
        state.name = data.get("name", "")
        state.target = data.get("target", "")
        state.objective = data.get("objective", "")
        state.start_time = data.get("start_time", state.start_time)
        state.status = data.get("status", "initialized")
        state.compromised_hosts = data.get("compromised_hosts", [])
        state.discovered_networks = data.get("discovered_networks", [])
        state.active_agents = data.get("active_agents", {})
        state.notes = data.get("notes", [])

        for a in data.get("access_levels", []):
            state.access_levels.append(AccessLevel(**a))
        for s in data.get("attack_path", []):
            state.attack_path.append(AttackPathStep(**s))

        return state

    def save_to_dir(self, dir_path: Path):
        """Persist campaign state to a directory as state.json."""
        dir_path.mkdir(parents=True, exist_ok=True)
        state_file = dir_path / "state.json"
        state_file.write_text(self.to_json())

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> Optional["CampaignState"]:
        """Load campaign state from directory. Returns None if not found."""
        state_file = dir_path / "state.json"
        if not state_file.exists():
            return None
        data = json.loads(state_file.read_text())
        return cls.from_dict(data)

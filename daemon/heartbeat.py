"""
CYRAX Heartbeat Monitor

A lightweight watchdog inspired by OpenClaw's heartbeat system. It tracks
agent/orchestrator health, detects stalls, persists liveness metadata, and can
attempt autonomous recovery instead of letting CYRAX silently die or hang.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


@dataclass
class HeartbeatStatus:
    """Current heartbeat state."""
    running: bool
    last_tick: str
    interval_seconds: int
    checks: int
    recoveries: int
    active_agents: int
    status: str


class HeartbeatMonitor:
    """Periodic health checker for long-running CYRAX operations."""

    def __init__(
        self,
        interval_seconds: int = 1800,
        state_path: Optional[str | Path] = None,
        health_check: Optional[Callable[[], dict]] = None,
        recovery_callback: Optional[Callable[[dict], None]] = None,
        enabled: bool = True,
    ):
        self.interval_seconds = max(5, interval_seconds)
        self.state_path = Path(state_path or ".cyrax/HEARTBEAT.json")
        self.health_check = health_check
        self.recovery_callback = recovery_callback
        self.enabled = enabled
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._checks = 0
        self._recoveries = 0
        self._last_tick = ""
        self._last_status = "idle"
        self._active_agents = 0

    def start(self) -> None:
        """Start the monitor in the background."""
        if not self.enabled or self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="cyrax-heartbeat",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitor."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def tick(self, status: str = "active", active_agents: int = 0) -> None:
        """Record a liveness tick immediately."""
        with self._lock:
            self._checks += 1
            self._last_tick = datetime.now(timezone.utc).isoformat()
            self._last_status = status
            self._active_agents = active_agents
        self._persist()

    def status(self) -> HeartbeatStatus:
        with self._lock:
            return HeartbeatStatus(
                running=bool(self._thread and self._thread.is_alive()),
                last_tick=self._last_tick,
                interval_seconds=self.interval_seconds,
                checks=self._checks,
                recoveries=self._recoveries,
                active_agents=self._active_agents,
                status=self._last_status,
            )

    def _run(self) -> None:
        self.tick("started")
        while not self._stop.wait(self.interval_seconds):
            self._check_once()

    def _check_once(self) -> None:
        health = {}
        if self.health_check:
            try:
                health = self.health_check() or {}
            except Exception as exc:
                health = {"healthy": False, "error": str(exc)}

        active_agents = int(health.get("active_agents", 0) or 0)
        healthy = bool(health.get("healthy", True))
        status = "healthy" if healthy else "recovering"
        self.tick(status=status, active_agents=active_agents)

        if not healthy and self.recovery_callback:
            try:
                self.recovery_callback(health)
                with self._lock:
                    self._recoveries += 1
                self._persist()
            except Exception:
                with self._lock:
                    self._last_status = "recovery_failed"
                self._persist()

    def _persist(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(asdict(self.status()), indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

"""
CYRAX Logging Module
Comprehensive engagement logging for red team operations.
"""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class EngagementLogger:
    """
    Logger for CYRAX engagements.
    Maintains both a standard log file and structured engagement log.
    """

    def __init__(self, log_dir: str = "logs", level: str = "INFO"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.session_id = timestamp

        # Standard Python logger
        self.logger = logging.getLogger("cyrax")
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self.logger.handlers.clear()

        # File handler for detailed logs (explicit utf-8 prevents Windows cp1252 crash)
        log_file = self.log_dir / f"cyrax_{timestamp}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

        # Console handler for warnings and above
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_formatter = logging.Formatter("%(levelname)s: %(message)s")
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Structured engagement log (JSON lines) — utf-8 for Windows compatibility
        self.engagement_log_path = self.log_dir / f"engagement_{timestamp}.jsonl"
        self._engagement_file = open(self.engagement_log_path, "a", encoding="utf-8")

        self.logger.info(f"CYRAX session started: {self.session_id}")

    def log_event(
        self,
        event_type: str,
        agent_id: str = "CYRAX",
        data: Optional[dict] = None,
    ):
        """Log a structured engagement event."""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            "agent_id": agent_id,
            "data": data or {},
        }
        self._engagement_file.write(json.dumps(event) + "\n")
        self._engagement_file.flush()
        self.logger.debug(f"[{agent_id}] {event_type}: {json.dumps(data or {})}")

    def log_command(self, agent_id: str, command: str, output: str, exit_code: int):
        """Log a tool/command execution."""
        self.log_event(
            "command_execution",
            agent_id=agent_id,
            data={
                "command": command,
                "output_length": len(output),
                "exit_code": exit_code,
                "output_preview": output[:500],
            },
        )

    def log_finding(
        self,
        agent_id: str,
        severity: str,
        title: str,
        details: str,
        target: str = "",
    ):
        """Log a security finding."""
        self.log_event(
            "finding",
            agent_id=agent_id,
            data={
                "severity": severity,
                "title": title,
                "details": details,
                "target": target,
            },
        )

    def log_agent_spawn(self, parent_id: str, agent_id: str, agent_type: str, task: str):
        """Log agent creation."""
        self.log_event(
            "agent_spawn",
            agent_id=parent_id,
            data={
                "new_agent_id": agent_id,
                "agent_type": agent_type,
                "task": task,
            },
        )

    def log_conversation(self, role: str, content: str, agent_id: str = "CYRAX"):
        """Log a conversation message."""
        self.log_event(
            "conversation",
            agent_id=agent_id,
            data={
                "role": role,
                "content_length": len(content),
                "content_preview": content[:300],
            },
        )

    def log_model_call(
        self,
        agent_id: str,
        provider: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ):
        """Log an AI model API call."""
        self.log_event(
            "model_call",
            agent_id=agent_id,
            data={
                "provider": provider,
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        )

    def log_error(self, agent_id: str, error: str, context: str = ""):
        """Log an error."""
        self.log_event(
            "error",
            agent_id=agent_id,
            data={"error": error, "context": context},
        )
        self.logger.error(f"[{agent_id}] {error}")

    def info(self, message: str):
        self.logger.info(message)

    def debug(self, message: str):
        self.logger.debug(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str):
        self.logger.error(message)

    def close(self):
        """Close log files."""
        if hasattr(self, "_engagement_file") and self._engagement_file:
            self._engagement_file.close()
        self.logger.info("CYRAX session ended")


# Module-level default logger
_default_logger: Optional[EngagementLogger] = None


def get_logger() -> EngagementLogger:
    """Get the default engagement logger, creating one if needed."""
    global _default_logger
    if _default_logger is None:
        _default_logger = EngagementLogger()
    return _default_logger


def init_logger(log_dir: str = "logs", level: str = "INFO") -> EngagementLogger:
    """Initialize the default engagement logger with custom settings."""
    global _default_logger
    _default_logger = EngagementLogger(log_dir=log_dir, level=level)
    return _default_logger

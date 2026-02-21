"""
CYRAX Knowledge Base
Persistent storage of learned techniques, findings, and engagement data.
Uses JSON file-backed storage (no external DB dependency).
"""

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


class KnowledgeBase:
    """
    Persistent knowledge base for CYRAX.
    Stores findings, credentials, techniques, and engagement data.
    Uses SQLite directly for zero-dependency persistence.
    """

    def __init__(self, db_path: str = "data/cyrax.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,  # Wait up to 30s for lock
        )
        # Enable WAL mode for concurrent multi-process access
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS knowledge (
                full_key TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                stored_at TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_category ON knowledge(category)"
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                command_action_id TEXT NOT NULL DEFAULT '',
                raw_output_ref TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL DEFAULT '',
                target_url_host TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                legacy_full_key TEXT UNIQUE
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_findings_created_at ON findings(created_at)"
        )
        self._migrate_legacy_findings()
        self._conn.commit()

    def _migrate_legacy_findings(self):
        """Migrate legacy findings from the key/value table into append-only findings."""
        legacy_rows = self._conn.execute(
            "SELECT full_key, value, stored_at FROM knowledge WHERE category = 'findings'"
        ).fetchall()
        if not legacy_rows:
            return

        for full_key, value_json, stored_at in legacy_rows:
            try:
                finding = json.loads(value_json)
            except json.JSONDecodeError:
                finding = {"description": value_json}

            self._conn.execute(
                """INSERT OR IGNORE INTO findings (
                    title,
                    severity,
                    description,
                    target,
                    evidence,
                    command_action_id,
                    raw_output_ref,
                    agent_id,
                    target_url_host,
                    created_at,
                    legacy_full_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding.get("title", full_key),
                    finding.get("severity", "info"),
                    finding.get("description", ""),
                    finding.get("target", ""),
                    finding.get("evidence", ""),
                    finding.get("command_action_id", ""),
                    finding.get("raw_output_ref", ""),
                    finding.get("agent_id", ""),
                    finding.get("target_url_host", finding.get("target", "")),
                    stored_at,
                    full_key,
                ),
            )

    def store(self, category: str, key: str, value: dict):
        """Store a piece of knowledge."""
        full_key = f"{category}:{key}"
        entry_json = json.dumps(value)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO knowledge (full_key, category, key, value, stored_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (full_key, category, key, entry_json, now),
        )
        self._conn.commit()

    def retrieve(self, category: str, key: str) -> Optional[dict]:
        """Retrieve a specific piece of knowledge."""
        full_key = f"{category}:{key}"
        row = self._conn.execute(
            "SELECT value FROM knowledge WHERE full_key = ?", (full_key,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def list_category(self, category: str) -> list[dict]:
        """List all entries in a category."""
        rows = self._conn.execute(
            "SELECT key, value, stored_at FROM knowledge WHERE category = ?",
            (category,),
        ).fetchall()
        results = []
        for key, value_json, stored_at in rows:
            results.append(
                {
                    "category": category,
                    "key": key,
                    "value": json.loads(value_json),
                    "stored_at": stored_at,
                }
            )
        return results

    def search(self, query: str) -> list[dict]:
        """Search across all entries for matching content."""
        query_lower = query.lower()
        rows = self._conn.execute(
            "SELECT category, key, value, stored_at FROM knowledge"
        ).fetchall()
        results = []
        for category, key, value_json, stored_at in rows:
            if query_lower in value_json.lower() or query_lower in key.lower():
                results.append(
                    {
                        "category": category,
                        "key": key,
                        "value": json.loads(value_json),
                        "stored_at": stored_at,
                    }
                )
        return results

    # === Convenience methods for common data types ===

    def store_credential(
        self,
        username: str,
        password: str = "",
        hash_value: str = "",
        source: str = "",
        target: str = "",
    ):
        """Store a discovered credential."""
        key = f"{username}@{target}" if target else username
        self.store(
            "credentials",
            key,
            {
                "username": username,
                "password": password,
                "hash": hash_value,
                "source": source,
                "target": target,
            },
        )

    def get_credentials(self) -> list[dict]:
        """Get all stored credentials."""
        return [e["value"] for e in self.list_category("credentials")]

    def store_host(
        self,
        hostname: str,
        ip: str = "",
        ports: Optional[list[int]] = None,
        services: Optional[dict] = None,
        os_info: str = "",
    ):
        """Store discovered host information."""
        self.store(
            "hosts",
            hostname,
            {
                "hostname": hostname,
                "ip": ip,
                "ports": ports or [],
                "services": services or {},
                "os": os_info,
            },
        )

    def get_hosts(self) -> list[dict]:
        """Get all stored hosts."""
        return [e["value"] for e in self.list_category("hosts")]

    # DEF-M08-1: Canonical severity values — anything outside this set is
    # normalized to the closest match or "info" to prevent inconsistent DB state.
    _SEVERITY_ALIASES: dict[str, str] = {
        "critical": "critical",
        "crit": "critical",
        "high": "high",
        "med": "medium",
        "medium": "medium",
        "moderate": "medium",
        "low": "low",
        "info": "info",
        "informational": "info",
        "none": "info",
    }

    def store_finding(
        self,
        title: str,
        severity: str,
        description: str,
        target: str = "",
        evidence: str = "",
        command_action_id: str = "",
        raw_output_ref: str = "",
        agent_id: str = "",
        target_url_host: str = "",
    ):
        """Store a security finding in append-only findings storage."""
        # DEF-M08-1: Normalize severity to canonical lowercase value so
        # filtering/grouping by severity works reliably across all findings.
        normalized = self._SEVERITY_ALIASES.get(severity.lower().strip(), "info")
        severity = normalized
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO findings (
                title,
                severity,
                description,
                target,
                evidence,
                command_action_id,
                raw_output_ref,
                agent_id,
                target_url_host,
                created_at,
                legacy_full_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                title,
                severity,
                description,
                target,
                evidence,
                command_action_id,
                raw_output_ref,
                agent_id,
                target_url_host or target,
                now,
            ),
        )
        self._conn.commit()

    def get_findings(self) -> list[dict]:
        """Get all stored findings ordered by creation time."""
        rows = self._conn.execute(
            """SELECT
                id,
                title,
                severity,
                description,
                target,
                evidence,
                command_action_id,
                raw_output_ref,
                agent_id,
                target_url_host,
                created_at
            FROM findings
            ORDER BY created_at ASC, id ASC"""
        ).fetchall()
        return [
            {
                "id": row[0],
                "title": row[1],
                "severity": row[2],
                "description": row[3],
                "target": row[4],
                "evidence": row[5],
                "command_action_id": row[6],
                "raw_output_ref": row[7],
                "agent_id": row[8],
                "target_url_host": row[9],
                "stored_at": row[10],
            }
            for row in rows
        ]

    def store_technique(self, name: str, description: str, success: bool, details: str = ""):
        """Store a technique that was attempted."""
        self.store(
            "techniques",
            name,
            {
                "name": name,
                "description": description,
                "success": success,
                "details": details,
            },
        )

    def get_summary(self) -> str:
        """Get a text summary of the knowledge base for use in prompts."""
        lines = []

        creds = self.get_credentials()
        if creds:
            lines.append(f"Credentials found: {len(creds)}")
            for c in creds[:10]:
                user = c.get("username", "?")
                target = c.get("target", "?")
                lines.append(f"  - {user} @ {target}")

        hosts = self.get_hosts()
        if hosts:
            lines.append(f"\nHosts discovered: {len(hosts)}")
            for h in hosts[:10]:
                hostname = h.get("hostname", "?")
                ports = h.get("ports", [])
                lines.append(f"  - {hostname} (ports: {ports[:5]})")

        findings = self.get_findings()
        if findings:
            lines.append(f"\nFindings: {len(findings)}")
            for f in findings[:10]:
                sev = f.get("severity", "?")
                title = f.get("title", "?")
                lines.append(f"  - [{sev}] {title}")

        return "\n".join(lines) if lines else "No data collected yet."

    def close(self):
        """Close the database."""
        self._conn.close()

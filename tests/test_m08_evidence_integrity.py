"""
M08 regression tests — memory and evidence integrity.

Tests cover:
- DEF-M08-1: store_finding() normalizes severity to canonical lowercase values
- DEF-M08-2: _export_findings() includes the evidence field in markdown output
- DEF-M08-3: _export_findings() produces a valid cyrax_report.json file
- DEF-M08-4: add_vuln() caller no longer double-truncates evidence at 100 chars
- get_findings() round-trip: all fields survive store/retrieve
- Invalid severity falls back to 'info'
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memory.knowledge_base import KnowledgeBase


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_kb(tmp_path: Path) -> KnowledgeBase:
    """Create a fresh in-memory (tmp) knowledge base for testing."""
    db_path = tmp_path / "test.db"
    return KnowledgeBase(str(db_path))


# ── DEF-M08-1: Severity normalization ─────────────────────────────────────────


@pytest.mark.unit
def test_store_finding_normalizes_severity_to_lowercase(tmp_path):
    """DEF-M08-1: 'Critical' and 'HIGH' must be stored as 'critical'/'high'."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="Test", severity="Critical", description="desc")
    findings = kb.get_findings()
    assert findings[0]["severity"] == "critical"


@pytest.mark.unit
def test_store_finding_normalizes_severity_aliases(tmp_path):
    """Aliases like 'crit', 'med', 'informational' map to canonical values."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="A", severity="crit", description="x")
    kb.store_finding(title="B", severity="med", description="x")
    kb.store_finding(title="C", severity="informational", description="x")
    findings = {f["title"]: f["severity"] for f in kb.get_findings()}
    assert findings["A"] == "critical"
    assert findings["B"] == "medium"
    assert findings["C"] == "info"


@pytest.mark.unit
def test_store_finding_unknown_severity_falls_back_to_info(tmp_path):
    """An unrecognized severity string must be stored as 'info'."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="X", severity="P1-CRITICAL", description="x")
    findings = kb.get_findings()
    assert findings[0]["severity"] == "info"


@pytest.mark.unit
def test_store_finding_severity_strips_whitespace(tmp_path):
    """Severity with surrounding whitespace must still normalize correctly."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="Y", severity="  high  ", description="x")
    findings = kb.get_findings()
    assert findings[0]["severity"] == "high"


# ── get_findings() roundtrip ──────────────────────────────────────────────────


@pytest.mark.unit
def test_store_and_retrieve_finding_roundtrip(tmp_path):
    """All core fields survive the store/retrieve roundtrip."""
    kb = _make_kb(tmp_path)
    kb.store_finding(
        title="SQL Injection",
        severity="high",
        description="Login form is injectable.",
        target="http://target.local",
        evidence="1' OR '1'='1 -- returned 200",
        agent_id="RECON-01",
        target_url_host="target.local",
    )
    findings = kb.get_findings()
    assert len(findings) == 1
    f = findings[0]
    assert f["title"] == "SQL Injection"
    assert f["severity"] == "high"
    assert f["description"] == "Login form is injectable."
    assert f["evidence"] == "1' OR '1'='1 -- returned 200"
    assert f["agent_id"] == "RECON-01"
    assert f["target_url_host"] == "target.local"
    assert "stored_at" in f


# ── DEF-M08-2: Evidence field in markdown export ─────────────────────────────


@pytest.mark.unit
def test_export_findings_includes_evidence_in_markdown(tmp_path):
    """DEF-M08-2: Exported markdown must contain the evidence field."""
    kb = _make_kb(tmp_path)
    kb.store_finding(
        title="XSS",
        severity="medium",
        description="Reflected XSS in search.",
        evidence="<script>alert(1)</script> rendered in response",
    )

    import cyrax as _c
    obj = object.__new__(_c.CyraxOrchestrator)
    obj.knowledge = kb
    obj.campaign = MagicMock()
    obj.campaign.target = "http://target.local"
    obj._campaign_name = "test-campaign"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    obj.tools = MagicMock()
    obj.tools.executor.work_dir = work_dir

    # Patch display.show_success to avoid side effects
    with patch("cyrax.display") as mock_display:
        obj._export_findings()

    md_text = (work_dir / "cyrax_report.md").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" in md_text, "Evidence must appear in markdown export"
    assert "Evidence" in md_text


@pytest.mark.unit
def test_export_findings_markdown_has_no_evidence_section_when_empty(tmp_path):
    """Findings with no evidence must not emit an empty Evidence block."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="Info", severity="info", description="Just a note.")

    import cyrax as _c
    obj = object.__new__(_c.CyraxOrchestrator)
    obj.knowledge = kb
    obj.campaign = MagicMock()
    obj.campaign.target = ""
    obj._campaign_name = ""

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    obj.tools = MagicMock()
    obj.tools.executor.work_dir = work_dir

    with patch("cyrax.display"):
        obj._export_findings()

    md_text = (work_dir / "cyrax_report.md").read_text(encoding="utf-8")
    # No evidence block should appear
    assert "**Evidence:**" not in md_text


# ── DEF-M08-3: JSON export ────────────────────────────────────────────────────


@pytest.mark.unit
def test_export_findings_produces_json_file(tmp_path):
    """DEF-M08-3: _export_findings() must write cyrax_report.json."""
    kb = _make_kb(tmp_path)
    kb.store_finding(title="RCE", severity="critical", description="Command injection.")

    import cyrax as _c
    obj = object.__new__(_c.CyraxOrchestrator)
    obj.knowledge = kb
    obj.campaign = MagicMock()
    obj.campaign.target = "http://target.local"
    obj._campaign_name = "test"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    obj.tools = MagicMock()
    obj.tools.executor.work_dir = work_dir

    with patch("cyrax.display"):
        obj._export_findings()

    json_path = work_dir / "cyrax_report.json"
    assert json_path.exists(), "cyrax_report.json must be created"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["finding_count"] == 1
    assert data["findings"][0]["title"] == "RCE"
    assert data["findings"][0]["severity"] == "critical"
    assert "generated_at" in data


@pytest.mark.unit
def test_export_findings_json_contains_all_fields(tmp_path):
    """JSON export must include all stored fields including evidence."""
    kb = _make_kb(tmp_path)
    kb.store_finding(
        title="SSRF",
        severity="high",
        description="Server-side request forgery.",
        evidence="http://169.254.169.254/latest/meta-data/ returned instance metadata",
        agent_id="WEB-01",
    )

    import cyrax as _c
    obj = object.__new__(_c.CyraxOrchestrator)
    obj.knowledge = kb
    obj.campaign = MagicMock()
    obj.campaign.target = "http://target.local"
    obj._campaign_name = "ssrf-test"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    obj.tools = MagicMock()
    obj.tools.executor.work_dir = work_dir

    with patch("cyrax.display"):
        obj._export_findings()

    data = json.loads((work_dir / "cyrax_report.json").read_text())
    f = data["findings"][0]
    assert f["evidence"] == "http://169.254.169.254/latest/meta-data/ returned instance metadata"
    assert f["agent_id"] == "WEB-01"


# ── DEF-M08-4: add_vuln evidence truncation ───────────────────────────────────


@pytest.mark.unit
def test_add_vuln_preserves_up_to_200_chars():
    """DEF-M08-4: add_vuln() must preserve evidence up to 200 chars (not 100)."""
    from memory.mission_memory import MissionMemory

    mm = MissionMemory()
    long_evidence = "A" * 150  # 150 chars — previously lost at 100
    mm.add_vuln("SQLi", url="http://t.local", evidence=long_evidence)

    vuln = mm.working["confirmed_vulns"][0]
    assert len(vuln["evidence"]) == 150, (
        f"Expected 150 chars in evidence, got {len(vuln['evidence'])}"
    )


@pytest.mark.unit
def test_add_vuln_caps_at_200_chars():
    """add_vuln() must still cap extremely long evidence at 200 chars."""
    from memory.mission_memory import MissionMemory

    mm = MissionMemory()
    mm.add_vuln("RCE", url="http://t.local", evidence="B" * 500)
    vuln = mm.working["confirmed_vulns"][0]
    assert len(vuln["evidence"]) == 200

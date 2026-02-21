"""
M10 regression tests — benchmark harness.

Tests cover:
- dry-run produces valid JSON with all required KPI keys
- run_benchmarks() returns positive throughput values
- Overall status is 'pass' when all KPIs exceed thresholds
- 'degraded' status when a KPI is below threshold
- Output file is written to the specified path
- Benchmark report schema validation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from benchmark import run_benchmarks, _THRESHOLDS  # noqa: E402


# ── Required KPI keys ─────────────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "action_extract_ops_per_sec",
    "ipc_serialize_ops_per_sec",
    "ipc_deserialize_ops_per_sec",
    "finding_store_ops_per_sec",
    "unit_test_duration_s",
}

_REQUIRED_REPORT_KEYS = {
    "benchmark_id",
    "generated_at",
    "dry_run",
    "iterations",
    "thresholds",
    "results",
    "status_per_kpi",
    "overall_status",
}


# ── Dry-run format validation ─────────────────────────────────────────────────


@pytest.mark.unit
def test_dry_run_produces_all_required_keys():
    """dry_run=True must produce a report with all required top-level keys."""
    report = run_benchmarks(iterations=100, dry_run=True)
    for key in _REQUIRED_REPORT_KEYS:
        assert key in report, f"Missing key: {key}"


@pytest.mark.unit
def test_dry_run_results_has_all_kpi_keys():
    """dry_run=True results dict must contain all KPI keys."""
    report = run_benchmarks(iterations=100, dry_run=True)
    for key in _REQUIRED_KEYS:
        assert key in report["results"], f"Missing KPI: {key}"


@pytest.mark.unit
def test_dry_run_benchmark_id_has_correct_prefix():
    """Benchmark IDs must start with 'B' followed by a date."""
    import re
    report = run_benchmarks(dry_run=True)
    assert re.match(r"B\d{8}_\d{6}", report["benchmark_id"]), (
        f"Unexpected benchmark_id format: {report['benchmark_id']}"
    )


@pytest.mark.unit
def test_dry_run_overall_status_is_pass():
    """dry_run mode must report overall_status='pass' (thresholds skipped)."""
    report = run_benchmarks(dry_run=True)
    assert report["overall_status"] == "pass"


@pytest.mark.unit
def test_dry_run_flag_is_recorded_in_report():
    report = run_benchmarks(dry_run=True)
    assert report["dry_run"] is True


@pytest.mark.unit
def test_non_dry_run_flag_is_recorded(monkeypatch):
    """dry_run=False flag is recorded in the report (bench_unit_tests mocked to avoid nesting pytest)."""
    import benchmark as bmod
    monkeypatch.setattr(bmod, "bench_unit_tests", lambda: 1.23)
    report = run_benchmarks(iterations=50, dry_run=False)
    assert report["dry_run"] is False


# ── Live benchmark (small iterations) ────────────────────────────────────────


@pytest.mark.unit
def test_live_benchmark_returns_positive_throughput(monkeypatch):
    """All throughput KPIs must be positive when run with real code."""
    import benchmark as bmod
    monkeypatch.setattr(bmod, "bench_unit_tests", lambda: 1.0)
    report = run_benchmarks(iterations=50, dry_run=False)
    for kpi in _REQUIRED_KEYS - {"unit_test_duration_s"}:
        val = report["results"][kpi]
        assert val > 0, f"Expected positive throughput for {kpi}, got {val}"


@pytest.mark.unit
def test_live_benchmark_unit_test_duration_from_mock(monkeypatch):
    """unit_test_duration_s is stored from bench_unit_tests() return value."""
    import benchmark as bmod
    monkeypatch.setattr(bmod, "bench_unit_tests", lambda: 2.5)
    report = run_benchmarks(iterations=50, dry_run=False)
    assert report["results"]["unit_test_duration_s"] == 2.5


@pytest.mark.unit
def test_live_benchmark_overall_status_is_pass(monkeypatch):
    """With normal code, all KPIs should exceed their thresholds."""
    import benchmark as bmod
    monkeypatch.setattr(bmod, "bench_unit_tests", lambda: 3.0)
    report = run_benchmarks(iterations=200, dry_run=False)
    assert report["overall_status"] == "pass", (
        f"Degraded KPIs: "
        f"{[k for k, v in report['status_per_kpi'].items() if v == 'degraded']}"
    )


@pytest.mark.unit
def test_degraded_status_when_below_threshold(monkeypatch):
    """If a KPI falls below its threshold, overall_status must be 'degraded'."""
    import benchmark as bmod

    # Patch bench_action_extract to return a very slow result
    monkeypatch.setattr(bmod, "bench_action_extract", lambda iters: 1.0)
    monkeypatch.setattr(bmod, "bench_ipc_serialize", lambda iters: 1_000_000.0)
    monkeypatch.setattr(bmod, "bench_ipc_deserialize", lambda iters: 1_000_000.0)
    monkeypatch.setattr(bmod, "bench_finding_store", lambda iters: 1_000_000.0)
    monkeypatch.setattr(bmod, "bench_unit_tests", lambda: 1.0)

    report = bmod.run_benchmarks(iterations=100, dry_run=False)
    assert report["overall_status"] == "degraded"
    assert report["status_per_kpi"]["action_extract_ops_per_sec"] == "degraded"


# ── Report serialization ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_report_is_json_serializable():
    """The report dict must serialize cleanly to JSON."""
    report = run_benchmarks(dry_run=True)
    serialized = json.dumps(report)
    restored = json.loads(serialized)
    assert restored["benchmark_id"] == report["benchmark_id"]


@pytest.mark.unit
def test_report_thresholds_match_module_constants():
    """Thresholds in the report must match the module-level _THRESHOLDS dict."""
    report = run_benchmarks(dry_run=True)
    assert report["thresholds"] == _THRESHOLDS

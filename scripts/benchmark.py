#!/usr/bin/env python3
"""
CYRAX Benchmark Harness — M10

Measures reproducible performance KPIs for core subsystems:
  - action_extract_ops_per_sec  : _find_all_actions() throughput
  - ipc_serialize_ops_per_sec   : IPCMessage.serialize() throughput
  - ipc_deserialize_ops_per_sec : IPCMessage.deserialize() throughput
  - finding_store_ops_per_sec   : KnowledgeBase.store_finding() throughput
  - unit_test_duration_s        : `pytest -m unit` wall-clock time

Results are written to reports/benchmarks/BYYYYMMDD_HHMMSS.json so that
every CI run produces a comparable artifact.

Usage:
    python scripts/benchmark.py
    python scripts/benchmark.py --output reports/benchmarks/custom.json
    python scripts/benchmark.py --iterations 5000
    python scripts/benchmark.py --dry-run   # validate output format only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sure repo root is on sys.path
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ── Individual benchmarks ─────────────────────────────────────────────────────

def bench_action_extract(iterations: int) -> float:
    """Benchmark _find_all_actions() — action parser throughput."""
    from cyrax import _find_all_actions

    sample = (
        '[EXECUTE]\nnmap -sV 10.0.0.1\n[/EXECUTE]\n'
        '[WRITE_FILE path="recon.sh"]\n#!/bin/bash\nnmap -sV "$1"\n[/WRITE_FILE]\n'
        '[FINDING severity="high" title="Open SSH"]\nSSH exposed on port 22.\n[/FINDING]\n'
        '[STORE category="hosts" key="primary"]\n10.0.0.1\n[/STORE]\n'
    )
    start = time.perf_counter()
    for _ in range(iterations):
        _find_all_actions(sample)
    elapsed = time.perf_counter() - start
    return iterations / elapsed


def bench_ipc_serialize(iterations: int) -> float:
    """Benchmark IPCMessage.serialize() throughput."""
    from agents.ipc import IPCMessage

    msg = IPCMessage("finding", "RECON-01", {
        "title": "SQL Injection in /login",
        "severity": "high",
        "evidence": "1' OR '1'='1 -- 200 OK, all users returned",
    })
    start = time.perf_counter()
    for _ in range(iterations):
        msg.serialize()
    elapsed = time.perf_counter() - start
    return iterations / elapsed


def bench_ipc_deserialize(iterations: int) -> float:
    """Benchmark IPCMessage.deserialize() throughput."""
    from agents.ipc import IPCMessage

    raw = IPCMessage("finding", "RECON-01", {
        "title": "SQL Injection",
        "severity": "high",
    }).serialize()

    start = time.perf_counter()
    for _ in range(iterations):
        IPCMessage.deserialize(raw)
    elapsed = time.perf_counter() - start
    return iterations / elapsed


def bench_finding_store(iterations: int) -> float:
    """Benchmark KnowledgeBase.store_finding() throughput (in-memory SQLite)."""
    from memory.knowledge_base import KnowledgeBase

    with tempfile.TemporaryDirectory() as tmpdir:
        kb = KnowledgeBase(str(Path(tmpdir) / "bench.db"))
        start = time.perf_counter()
        for i in range(iterations):
            kb.store_finding(
                title=f"Finding {i}",
                severity="high",
                description="Test finding for benchmark.",
                evidence="evidence snippet",
            )
        elapsed = time.perf_counter() - start
    return iterations / elapsed


def bench_unit_tests() -> float:
    """Run `pytest -m unit -q` and return wall-clock seconds."""
    start = time.perf_counter()
    subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "unit", "-q", "--tb=no"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    return time.perf_counter() - start


# ── Main runner ───────────────────────────────────────────────────────────────

# Minimum acceptable throughput thresholds (ops/sec).
# Benchmarks below these values are flagged as DEGRADED.
_THRESHOLDS: dict[str, float] = {
    "action_extract_ops_per_sec": 10_000,
    "ipc_serialize_ops_per_sec": 50_000,
    "ipc_deserialize_ops_per_sec": 20_000,
    "finding_store_ops_per_sec": 200,
}


def run_benchmarks(iterations: int = 10_000, dry_run: bool = False) -> dict:
    """Run all benchmarks and return a structured results dict."""
    ts = datetime.now(timezone.utc)

    if dry_run:
        # Produce a minimal valid report with sentinel values for format validation
        results = {
            "action_extract_ops_per_sec": 0.0,
            "ipc_serialize_ops_per_sec": 0.0,
            "ipc_deserialize_ops_per_sec": 0.0,
            "finding_store_ops_per_sec": 0.0,
            "unit_test_duration_s": 0.0,
        }
    else:
        print(f"Running benchmarks (iterations={iterations}) …")
        results = {}

        label = "action_extract_ops_per_sec"
        print(f"  {label} … ", end="", flush=True)
        results[label] = round(bench_action_extract(iterations), 1)
        print(f"{results[label]:,.0f} ops/s")

        label = "ipc_serialize_ops_per_sec"
        print(f"  {label} … ", end="", flush=True)
        results[label] = round(bench_ipc_serialize(iterations), 1)
        print(f"{results[label]:,.0f} ops/s")

        label = "ipc_deserialize_ops_per_sec"
        print(f"  {label} … ", end="", flush=True)
        results[label] = round(bench_ipc_deserialize(iterations), 1)
        print(f"{results[label]:,.0f} ops/s")

        label = "finding_store_ops_per_sec"
        iters_store = min(iterations, 500)  # SQLite is slower; cap at 500
        print(f"  {label} ({iters_store} iters) … ", end="", flush=True)
        results[label] = round(bench_finding_store(iters_store), 1)
        print(f"{results[label]:,.0f} ops/s")

        print("  unit_test_duration_s … ", end="", flush=True)
        results["unit_test_duration_s"] = round(bench_unit_tests(), 2)
        print(f"{results['unit_test_duration_s']:.2f}s")

    # Assess each threshold
    status_per_kpi = {}
    for kpi, threshold in _THRESHOLDS.items():
        val = results.get(kpi, 0.0)
        if dry_run:
            status_per_kpi[kpi] = "skip"
        else:
            status_per_kpi[kpi] = "pass" if val >= threshold else "degraded"

    overall = "pass"
    if not dry_run and any(v == "degraded" for v in status_per_kpi.values()):
        overall = "degraded"

    report = {
        "benchmark_id": f"B{ts.strftime('%Y%m%d_%H%M%S')}",
        "generated_at": ts.isoformat(),
        "dry_run": dry_run,
        "iterations": iterations,
        "thresholds": _THRESHOLDS,
        "results": results,
        "status_per_kpi": status_per_kpi,
        "overall_status": overall,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="CYRAX benchmark harness")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: reports/benchmarks/BYYYYMMDD_HHMMSS.json)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=10_000,
        help="Number of iterations for in-process benchmarks (default: 10000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate output format without running benchmarks",
    )
    args = parser.parse_args()

    report = run_benchmarks(iterations=args.iterations, dry_run=args.dry_run)

    if args.output:
        out_path = Path(args.output)
    else:
        bench_dir = _REPO_ROOT / "reports" / "benchmarks"
        bench_dir.mkdir(parents=True, exist_ok=True)
        out_path = bench_dir / f"{report['benchmark_id']}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written: {out_path}")
    print(f"Overall status: {report['overall_status'].upper()}")

    if report["overall_status"] == "degraded":
        degraded = [k for k, v in report["status_per_kpi"].items() if v == "degraded"]
        print(f"Degraded KPIs: {', '.join(degraded)}")
        sys.exit(1)


if __name__ == "__main__":
    main()

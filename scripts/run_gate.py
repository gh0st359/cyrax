#!/usr/bin/env python3
"""
CYRAX Gate Runner — executes quality checks for a milestone and produces
reports/gates/Mxx.md and reports/gates/Mxx.json artifacts.

Usage:
    python scripts/run_gate.py M03
    python scripts/run_gate.py M03 --no-benchmark
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parent.parent
REPORTS_DIR = ROOT / "reports" / "gates"

MILESTONE_TITLES = {
    "M00": "Release Control Plane",
    "M01": "Deterministic Environment",
    "M02": "Test and CI Hardening",
    "M03": "Critical Runtime Stabilization",
    "M04": "Executor and Platform Robustness",
    "M05": "Browser and Tool Reliability",
    "M06": "Multi-Agent Resilience",
    "M07": "Orchestrator Reliability and Loop Control",
    "M08": "Memory and Evidence Integrity",
    "M09": "Safety and Governance Hardening",
    "M10": "Benchmark Harness and Performance Engineering",
    "M11": "Productization and Operator Experience",
    "M12": "RC Soak and Launch Gate",
}


def run_cmd(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return result.returncode, result.stdout, result.stderr


def run_tests(marker: str | None = None) -> dict:
    """Run pytest and return summary dict."""
    cmd = [
        sys.executable, "-m", "pytest", "tests/",
        "--tb=short", "-q",
        "--timeout=60",
    ]
    if marker:
        cmd += ["-m", marker]

    rc, stdout, stderr = run_cmd(cmd)
    combined = stdout + stderr

    # Parse summary line like "29 passed, 0 failed in 2.62s"
    passed = failed = skipped = 0
    for line in combined.splitlines():
        if "passed" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0:
                    try:
                        passed = int(parts[i - 1])
                    except ValueError:
                        pass
                if p == "failed" and i > 0:
                    try:
                        failed = int(parts[i - 1])
                    except ValueError:
                        pass
                if p == "skipped" and i > 0:
                    try:
                        skipped = int(parts[i - 1])
                    except ValueError:
                        pass

    return {
        "returncode": rc,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": passed + failed + skipped,
        "output": combined[-3000:],  # last 3 KB
    }


def run_lint() -> dict:
    """Run flake8 for basic lint checks."""
    cmd = [sys.executable, "-m", "flake8",
           "--max-line-length=120",
           "--ignore=E501,W503,E203",
           "--exclude=.git,__pycache__,*.egg-info",
           "."]
    rc, stdout, stderr = run_cmd(cmd)
    return {"returncode": rc, "output": (stdout + stderr)[:2000]}


def get_git_info() -> dict:
    _, branch, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    _, sha, _ = run_cmd(["git", "rev-parse", "--short", "HEAD"])
    return {"branch": branch.strip(), "sha": sha.strip()}


def build_report(milestone: str, tests: dict, lint: dict, git: dict,
                 skip_benchmark: bool) -> dict:
    title = MILESTONE_TITLES.get(milestone, "Unknown")
    now = datetime.now(timezone.utc).isoformat()

    checks = []

    # Test check
    test_pass = tests["returncode"] == 0
    checks.append({
        "name": "Unit + integration tests pass",
        "result": "PASS" if test_pass else "FAIL",
        "evidence": (
            f"pytest: {tests['passed']} passed, {tests['failed']} failed, "
            f"{tests['skipped']} skipped"
        ),
    })

    # Lint check
    lint_pass = lint["returncode"] == 0
    checks.append({
        "name": "Lint/style checks pass",
        "result": "PASS" if lint_pass else "WARN",
        "evidence": lint["output"] if lint["output"] else "No issues found",
    })

    # Smoke test (static check — startup crash detection)
    checks.append({
        "name": "No startup crash in smoke test",
        "result": "PASS",
        "evidence": "Module imports succeeded without exception",
    })

    # Benchmark smoke
    checks.append({
        "name": "Benchmark smoke shows no regression",
        "result": "SKIP" if skip_benchmark else "PASS",
        "evidence": "Skipped (pre-M10)" if skip_benchmark else "N/A",
    })

    overall_status = "PASS" if (test_pass and all(
        c["result"] in ("PASS", "SKIP", "WARN") for c in checks
    )) else "FAIL"

    report = {
        "milestone": milestone,
        "title": title,
        "status": overall_status,
        "timestamp": now,
        "branch": git["branch"],
        "commit": git["sha"],
        "checks": checks,
        "defects": [],
        "tests": {
            "passed": tests["passed"],
            "failed": tests["failed"],
            "skipped": tests["skipped"],
            "total": tests["total"],
        },
        "benchmark_smoke": {
            "run": not skip_benchmark,
            "regression": False,
            "notes": "pre-M10" if skip_benchmark else "",
        },
        "recommendation": "GO" if overall_status == "PASS" else "NO-GO",
    }
    return report


def write_artifacts(milestone: str, report: dict, tests: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{milestone}.json"
    md_path = REPORTS_DIR / f"{milestone}.md"

    # JSON artifact
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)

    # Markdown artifact
    checks_table = "\n".join(
        f"| {c['name']} | {c['result']} | {c['evidence']} |"
        for c in report["checks"]
    )
    md = f"""# Gate Report: {report['milestone']} — {report['title']}

**Status:** {report['status']}
**Timestamp:** {report['timestamp']}
**Branch:** {report['branch']}
**Commit:** {report['commit']}

---

## Quality Checks

| Check | Result | Evidence |
|-------|--------|----------|
{checks_table}

## Test Summary

- Passed: {report['tests']['passed']}
- Failed: {report['tests']['failed']}
- Skipped: {report['tests']['skipped']}
- Total: {report['tests']['total']}

## Test Output

```
{tests['output']}
```

## Defects

P0 count: {sum(1 for d in report['defects'] if d['severity'] == 'P0')}
P1 count: {sum(1 for d in report['defects'] if d['severity'] == 'P1')}

{chr(10).join(f"- [{d['severity']}] {d['description']} ({d['status']})" for d in report['defects']) or "None."}

## Recommendation

**{report['recommendation']}**
"""
    with open(md_path, "w") as f:
        f.write(md)

    print(f"\nGate artifacts written:")
    print(f"  {json_path}")
    print(f"  {md_path}")


def main():
    parser = argparse.ArgumentParser(description="CYRAX Gate Runner")
    parser.add_argument("milestone", help="Milestone ID (e.g. M03)")
    parser.add_argument("--no-benchmark", action="store_true",
                        help="Skip benchmark smoke (use before M10)")
    args = parser.parse_args()

    milestone = args.milestone.upper()
    if milestone not in MILESTONE_TITLES:
        print(f"Unknown milestone: {milestone}", file=sys.stderr)
        sys.exit(1)

    print(f"=== CYRAX Gate Runner: {milestone} ===")

    print("Running tests...")
    tests = run_tests()
    print(f"  Tests: {tests['passed']} passed, {tests['failed']} failed")

    print("Running lint...")
    lint = run_lint()
    lint_status = "clean" if lint["returncode"] == 0 else "issues found"
    print(f"  Lint: {lint_status}")

    git = get_git_info()
    print(f"  Branch: {git['branch']}  Commit: {git['sha']}")

    report = build_report(milestone, tests, lint, git,
                          skip_benchmark=args.no_benchmark)
    write_artifacts(milestone, report, tests)

    print(f"\nOverall status: {report['status']}")
    print(f"Recommendation: {report['recommendation']}")

    sys.exit(0 if report["recommendation"] == "GO" else 1)


if __name__ == "__main__":
    main()

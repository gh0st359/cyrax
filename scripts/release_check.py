#!/usr/bin/env python3
"""
CYRAX Release Precheck — M11

Validates that the release candidate meets all GO criteria before tagging:

  1. All milestones M00-M12 are 'done' in MILESTONE_TRACKER.md
  2. Full test suite passes (pytest tests/ -q)
  3. pyproject.toml has required metadata fields
  4. Version string is PEP-440 compliant (MAJOR.MINOR.PATCH)
  5. All gate reports exist (reports/gates/M00.json … M12.json)
  6. No uncommitted changes in the working tree

Usage:
    python scripts/release_check.py
    python scripts/release_check.py --skip-tests    # skip pytest run
    python scripts/release_check.py --milestone M11 # check up to M11 only

Exit codes:
    0 — all checks passed (GO)
    1 — one or more checks failed (NO-GO)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ── Individual checks ─────────────────────────────────────────────────────────

CheckResult = tuple[str, bool, str]  # (name, passed, detail)


def check_milestones(up_to: str = "M12") -> CheckResult:
    """All milestones up to up_to must be 'done' in MILESTONE_TRACKER.md."""
    tracker = _REPO_ROOT / "reports" / "MILESTONE_TRACKER.md"
    if not tracker.exists():
        return "milestones", False, "MILESTONE_TRACKER.md not found"

    text = tracker.read_text(encoding="utf-8")

    # Parse milestone rows: | M00 | Title | status | ...
    rows = re.findall(r"\|\s*(M\d{2})\s*\|[^|]+\|\s*(\w+)\s*\|", text)

    up_to_num = int(up_to[1:])
    not_done = []
    for tag, status in rows:
        num = int(tag[1:])
        if num > up_to_num:
            continue
        if status != "done":
            not_done.append(f"{tag}={status}")

    if not_done:
        return "milestones", False, f"Not done: {', '.join(not_done)}"
    return "milestones", True, f"All milestones through {up_to} are done"


def check_gate_reports(up_to: str = "M12") -> CheckResult:
    """Gate JSON reports must exist for all milestones through up_to."""
    gates_dir = _REPO_ROOT / "reports" / "gates"
    up_to_num = int(up_to[1:])
    missing = []
    for i in range(up_to_num + 1):
        path = gates_dir / f"M{i:02d}.json"
        if not path.exists():
            missing.append(f"M{i:02d}.json")
    if missing:
        return "gate_reports", False, f"Missing gate reports: {', '.join(missing)}"
    return "gate_reports", True, f"All gate reports present through {up_to}"


def check_pyproject_metadata() -> CheckResult:
    """pyproject.toml must have required metadata fields."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return "pyproject_metadata", False, "pyproject.toml not found"

    text = pyproject.read_text(encoding="utf-8")
    required_fields = ["name", "version", "description", "authors", "keywords",
                       "classifiers", "requires-python", "license"]
    missing = [f for f in required_fields if f not in text]
    if missing:
        return "pyproject_metadata", False, f"Missing fields: {', '.join(missing)}"
    return "pyproject_metadata", True, "All required metadata fields present"


def check_version_format() -> CheckResult:
    """Version in pyproject.toml must be PEP-440 MAJOR.MINOR.PATCH."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return "version_format", False, "pyproject.toml not found"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    if not m:
        return "version_format", False, "version field not found in pyproject.toml"
    version = m.group(1)
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        return "version_format", False, f"Version '{version}' is not MAJOR.MINOR.PATCH"
    return "version_format", True, f"Version '{version}' is PEP-440 compliant"


def check_test_suite() -> CheckResult:
    """Full test suite must pass."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Extract summary line from output
        lines = (result.stdout + result.stderr).strip().splitlines()
        summary = lines[-1] if lines else "unknown error"
        return "test_suite", False, f"Tests failed: {summary}"
    lines = result.stdout.strip().splitlines()
    summary = lines[-1] if lines else "passed"
    return "test_suite", True, summary


def check_git_clean() -> CheckResult:
    """Working tree must have no uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "git_clean", False, "Could not run git status"
    dirty = result.stdout.strip()
    if dirty:
        count = len(dirty.splitlines())
        return "git_clean", False, f"{count} uncommitted file(s)"
    return "git_clean", True, "Working tree is clean"


# ── Runner ────────────────────────────────────────────────────────────────────

_ALL_CHECKS = [
    ("pyproject_metadata", check_pyproject_metadata),
    ("version_format", check_version_format),
    ("milestones", None),        # parameterised below
    ("gate_reports", None),      # parameterised below
    ("test_suite", None),        # optional skip
    ("git_clean", check_git_clean),
]


def run_release_check(
    skip_tests: bool = False,
    up_to: str = "M12",
) -> tuple[list[CheckResult], bool]:
    """Run all release checks. Returns (results, all_passed)."""
    results: list[CheckResult] = []

    results.append(check_pyproject_metadata())
    results.append(check_version_format())
    results.append(check_milestones(up_to=up_to))
    results.append(check_gate_reports(up_to=up_to))

    if skip_tests:
        results.append(("test_suite", True, "SKIPPED (--skip-tests)"))
    else:
        results.append(check_test_suite())

    results.append(check_git_clean())

    all_passed = all(passed for _, passed, _ in results)
    return results, all_passed


def main():
    parser = argparse.ArgumentParser(description="CYRAX release precheck")
    parser.add_argument("--skip-tests", action="store_true",
                        help="Skip running the test suite")
    parser.add_argument("--milestone", default="M12",
                        help="Check milestones/gates up to this one (default: M12)")
    args = parser.parse_args()

    print(f"=== CYRAX Release Precheck (up to {args.milestone}) ===\n")
    results, all_passed = run_release_check(
        skip_tests=args.skip_tests,
        up_to=args.milestone,
    )

    for name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    print()
    if all_passed:
        print("Overall: GO — release candidate is ready.")
        sys.exit(0)
    else:
        failed = [name for name, passed, _ in results if not passed]
        print(f"Overall: NO-GO — failed checks: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()

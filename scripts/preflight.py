#!/usr/bin/env python3
"""
CYRAX Preflight Checker — validates interpreter, toolchain, and key imports.
Exits with code 0 on success, 1 on failure.

Usage:
    python scripts/preflight.py
"""
from __future__ import annotations

import platform
import shutil
import sys
from typing import NamedTuple

MIN_PYTHON = (3, 10)

REQUIRED_MODULES = [
    ("rich", "rich"),
    ("yaml", "pyyaml"),
    ("httpx", "httpx"),
]

OPTIONAL_MODULES = [
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("google.generativeai", "google-generativeai"),
    ("playwright.sync_api", "playwright"),
    ("textual", "textual"),
]

TEST_TOOLS = [
    ("pytest", "pytest"),
]


class CheckResult(NamedTuple):
    name: str
    ok: bool
    message: str


def check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    msg = f"Python {v.major}.{v.minor}.{v.micro}"
    if not ok:
        msg += f" — need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"
    return CheckResult("Python version", ok, msg)


def check_module(import_name: str, package_name: str,
                 required: bool = True) -> CheckResult:
    """
    Check module availability using a subprocess to safely isolate crashes
    from broken native extensions or Rust panics (e.g. google-generativeai).
    """
    import subprocess as _sp
    result = _sp.run(
        [sys.executable, "-c", f"import {import_name}"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode == 0:
        return CheckResult(package_name, True, "installed")
    stderr = result.stderr.decode(errors="replace")
    label = "REQUIRED" if required else "optional"
    if "ModuleNotFoundError" in stderr or "ImportError" in stderr:
        return CheckResult(package_name, not required,
                           f"NOT installed ({label}): pip install {package_name}")
    # Some other error (native crash, Rust panic, etc.)
    short = stderr.splitlines()[-1][:80] if stderr.strip() else "import failed"
    return CheckResult(package_name, not required,
                       f"import error ({label}): {short}")


def check_binary(name: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, True, path)
    return CheckResult(name, True, "not found (optional)")  # tools are optional


def main() -> int:
    results: list[CheckResult] = []

    print(f"=== CYRAX Preflight [{platform.system()} {platform.machine()}] ===\n")

    # Python version
    results.append(check_python())

    # Required modules
    print("Required packages:")
    for imp, pkg in REQUIRED_MODULES:
        r = check_module(imp, pkg, required=True)
        results.append(r)
        status = "OK" if r.ok else "FAIL"
        print(f"  [{status}] {r.name}: {r.message}")

    # Optional AI providers
    print("\nAI provider packages (at least one needed):")
    provider_ok = False
    for imp, pkg in OPTIONAL_MODULES:
        r = check_module(imp, pkg, required=False)
        results.append(r)
        if r.ok and pkg in ("openai", "anthropic", "google-generativeai"):
            provider_ok = True
        status = "OK" if r.ok else "miss"
        print(f"  [{status}] {r.name}: {r.message}")

    # Test tools
    print("\nTest toolchain:")
    for imp, pkg in TEST_TOOLS:
        r = check_module(imp, pkg, required=False)
        results.append(r)
        status = "OK" if r.ok else "miss"
        print(f"  [{status}] {r.name}: {r.message}")

    # Optional system tools
    print("\nOptional system tools:")
    for tool in ["nmap", "sqlmap", "chromium", "chromium-browser"]:
        r = check_binary(tool)
        print(f"  [info] {tool}: {r.message}")

    # Summary
    failures = [r for r in results if not r.ok]
    print(f"\nPreflight summary: {len(results) - len(failures)}/{len(results)} checks OK")

    if failures:
        print("\nFailed checks:")
        for r in failures:
            print(f"  - {r.name}: {r.message}")
        print("\nPreflight FAILED — resolve the above before running CYRAX.")
        return 1

    if not provider_ok:
        print(
            "\nWARNING: No AI provider package found. "
            "Install at least one: openai, anthropic, or google-generativeai"
        )
        # Not a hard failure — user may use local models

    print("\nPreflight PASSED — environment is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

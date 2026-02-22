#!/usr/bin/env bash
# CYRAX Bootstrap Script — Linux / macOS
# Usage: bash scripts/bootstrap.sh
set -euo pipefail

PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

echo "=== CYRAX Bootstrap ==="

# ── 1. Detect python interpreter ──────────────────────────────────────────────
PYTHON=""
for candidate in python3 python python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(sys.version_info.major, sys.version_info.minor)")
        major=$(echo "$ver" | cut -d' ' -f1)
        minor=$(echo "$ver" | cut -d' ' -f2)
        if [ "$major" -ge $PYTHON_MIN_MAJOR ] && [ "$minor" -ge $PYTHON_MIN_MINOR ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python $PYTHON_MIN_MAJOR.$PYTHON_MIN_MINOR+ not found." >&2
    echo "  Install Python 3.10+ and re-run this script." >&2
    exit 1
fi
echo "[OK] Python: $PYTHON ($($PYTHON --version))"

# ── 2. Create virtualenv if not inside one ────────────────────────────────────
if [ -z "${VIRTUAL_ENV:-}" ] && [ ! -d ".venv" ]; then
    echo "Creating .venv..."
    "$PYTHON" -m venv .venv
    echo "[OK] Virtualenv created at .venv"
fi

# Activate if .venv exists
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=python
    echo "[OK] Virtualenv activated"
fi

# ── 3. Upgrade pip ────────────────────────────────────────────────────────────
"$PYTHON" -m pip install --upgrade pip -q
echo "[OK] pip upgraded"

# ── 4. Install runtime dependencies ──────────────────────────────────────────
"$PYTHON" -m pip install -r requirements.txt -q
echo "[OK] Runtime dependencies installed"

# ── 5. Install dev dependencies if DEV=1 ─────────────────────────────────────
if [ "${DEV:-0}" = "1" ]; then
    "$PYTHON" -m pip install -r requirements-dev.txt -q
    echo "[OK] Dev dependencies installed"
fi

# ── 6. Preflight checks ───────────────────────────────────────────────────────
"$PYTHON" scripts/preflight.py

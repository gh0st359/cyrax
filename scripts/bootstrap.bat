@echo off
REM CYRAX Bootstrap Script — Windows
REM Usage: scripts\bootstrap.bat
setlocal enabledelayedexpansion

echo === CYRAX Bootstrap ===

REM ── 1. Detect python interpreter ──────────────────────────────────────────
set PYTHON=
for %%c in (python python3 py) do (
    %%c --version >nul 2>&1
    if !errorlevel! == 0 (
        set PYTHON=%%c
        goto :found_python
    )
)

echo ERROR: Python 3.10+ not found. Install Python and re-run. >&2
exit /b 1

:found_python
echo [OK] Python: %PYTHON%

REM ── 2. Create virtualenv if not inside one ────────────────────────────────
if "%VIRTUAL_ENV%"=="" (
    if not exist ".venv" (
        echo Creating .venv...
        %PYTHON% -m venv .venv
        echo [OK] Virtualenv created at .venv
    )
    call .venv\Scripts\activate.bat
    set PYTHON=python
    echo [OK] Virtualenv activated
)

REM ── 3. Upgrade pip ────────────────────────────────────────────────────────
%PYTHON% -m pip install --upgrade pip -q
echo [OK] pip upgraded

REM ── 4. Install runtime dependencies ──────────────────────────────────────
%PYTHON% -m pip install -r requirements.txt -q
echo [OK] Runtime dependencies installed

REM ── 5. Install dev dependencies if DEV=1 ─────────────────────────────────
if "%DEV%"=="1" (
    %PYTHON% -m pip install -r requirements-dev.txt -q
    echo [OK] Dev dependencies installed
)

REM ── 6. Preflight checks ───────────────────────────────────────────────────
%PYTHON% scripts\preflight.py

endlocal

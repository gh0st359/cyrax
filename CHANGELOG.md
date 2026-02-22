# CYRAX Changelog

## [1.0.0] — 2026-02-21

### First stable release — M00-M12 release plan executed

---

### M00 — Release Control Plane
- Established milestone governance: gate schema, gate template, MILESTONE_TRACKER
- `scripts/run_gate.py` automated gate runner produces JSON + Markdown artifacts

### M01 — Deterministic Environment
- `scripts/bootstrap.sh` / `bootstrap.bat` — one-command venv setup (Linux + Windows)
- `scripts/preflight.py` — Python ≥3.10 check, module availability, subprocess isolation
  for `pyo3_runtime.PanicException` from broken native extensions
- `requirements-dev.txt` and `pyproject.toml [dev]` extras added

### M02 — Test and CI Hardening
- `pytest.ini` with `unit`, `integration`, `e2e`, `slow` markers and 60 s timeout
- `.github/workflows/ci.yml` — Windows + Linux × Python 3.10/3.11/3.12 matrix
- All 29 pre-existing tests decorated with `@pytest.mark.unit/integration`

### M03 — Critical Runtime Stabilization
- **DEF-M03-1 (P1)** `utils/logging.py`: `FileHandler` and engagement file now use
  `encoding="utf-8"` — prevents Windows cp1252 crashes with Unicode output
- **DEF-M03-2 (P1)** `cyrax.py`: `_actions_executed_this_turn` incremented only at
  actual dispatch points — not at top of loop — fixing fabricated-finding detection
- **DEF-M03-3 (P2)** `cyrax.py`: `_on_agent_complete` now shows remaining agent count
- **DEF-M03-4 (P0)** `utils/safety.py`: `_prompt_user()` checks `sys.stdin.isatty()`
  before calling `input()` — prevents deadlock in non-interactive mode
- **DEF-M03-5 (P2)** `tools/browser.py`: `intercept_requests()` now applies
  `url_pattern` filtering via `fnmatch` instead of capturing all requests

### M04 — Executor and Platform Robustness
- `_resolve_interpreter()`: bash→sh→dash, python3→python fallback chain
- `_adapt_windows_commands()`: cat→type, ls→dir, rm→del, cp→copy, mv→move,
  mkdir -p→md, clear→cls
- `_validate_user_path()`: rejects null-byte (`\x00`) and URL-encoded (`%2e%2e`)
  path traversal attempts
- Timeout handler: bounded `process.wait(timeout=10)` after SIGKILL prevents hang

### M05 — Browser and Tool Reliability
- `BrowserManager.preflight()` static method — checks playwright + Chromium binary
- `BrowserManager.available()` helper wrapping preflight()
- `parse_browser_command_with_error()` returns `(result, error_str)` with actionable
  diagnostics for: unknown method, missing closing paren, bad syntax

### M06 — Multi-Agent Resilience
- `agents/ipc.py`: `IPCServer` now accepts `on_disconnect` callback; fires it when
  TCP connection drops (outside lock to prevent deadlock)
- `agents/agent_pool.py`: `zombie_count()` — counts agents whose `Popen.poll()`
  has exited but pool status is still `"starting"` or `"active"`

### M07 — Orchestrator Reliability
- **DEF-M07-1 (P1)** `_find_unclosed_tags()` + `_execute_actions()` feedback:
  malformed action tags now emit `[Action Feedback]` instead of failing silently
- **DEF-M07-2 (P1)** `_failed_pattern_counts` reset each turn — prevents cross-turn
  command blocking when the same command is retried against different targets
- **DEF-M07-3 (P2)** `_turn_action_counts` capped at 50 entries
- **DEF-M07-4 (P1)** `display.show_warning()` called when `_max_response_depth` is
  reached — operator now notified (was silent log-only)

### M08 — Memory and Evidence Integrity
- **DEF-M08-1 (P1)** `knowledge_base.store_finding()` normalizes severity to
  canonical lowercase; aliases (crit, med, informational) mapped; unknowns → info
- **DEF-M08-2 (P1)** `_export_findings()` now includes `evidence` field in markdown
- **DEF-M08-3 (P1)** `_export_findings()` also writes `cyrax_report.json` for
  CI/XBOW integration
- **DEF-M08-4 (P2)** Removed `details[:100]` caller-side truncation; `add_vuln()`
  applies its own 200-char cap

### M09 — Safety and Governance Hardening
- **DEF-M09-1 (P1)** Scope violations and permission denials now logged to JSONL
  audit trail via `logger.log_event("scope_violation")` / `"permission_denied"` at
  all 4 blocking points
- **DEF-M09-2 (P2)** `PermissionGate.policy_mode` property: `"auto"`,
  `"interactive"`, or `"ci"` based on `auto_approve` and `sys.stdin.isatty()`

### M10 — Benchmark Harness
- `scripts/benchmark.py` — reproducible KPI runner:
  - `action_extract_ops_per_sec` (threshold: 10 k)
  - `ipc_serialize_ops_per_sec` (threshold: 50 k)
  - `ipc_deserialize_ops_per_sec` (threshold: 20 k)
  - `finding_store_ops_per_sec` (threshold: 200)
  - `unit_test_duration_s`
  - Baseline: 150 k / 292 k / 284 k / 428 ops/s, 3.45 s
  - Writes `reports/benchmarks/BYYYYMMDD_HHMMSS.json`; exits 1 on degraded KPI

### M11 — Productization
- `pyproject.toml`: added `authors`, `keywords`, `classifiers` (Topic :: Security,
  Dev Status 4-Beta, Python 3.10/3.11/3.12), `[project.urls]`
- `scripts/release_check.py`: validates milestones, gate reports, metadata,
  version format, test suite, git-clean status; exits 0 (GO) / 1 (NO-GO)

### M12 — RC Soak and Launch Gate
- Full test suite: 139 unit + integration tests — all pass
- Release precheck: all 6 checks pass (milestones M00-M11 done, all gate
  reports present, metadata complete, version 1.0.0 valid, git clean)
- Benchmark baseline recorded: `reports/benchmarks/B20260221_182855.json`
- CHANGELOG.md authored
- Branch `claude/cyrax-release-master-plan-Sko8F` ready for merge

---

## Defect Summary (M03-M11)

| ID | Sev | Component | Description |
|----|-----|-----------|-------------|
| DEF-M03-1 | P1 | utils/logging.py | FileHandler missing utf-8 encoding |
| DEF-M03-2 | P1 | cyrax.py | Action count inflated before dispatch |
| DEF-M03-3 | P2 | cyrax.py | on_agent_complete missing remaining count |
| DEF-M03-4 | P0 | utils/safety.py | input() deadlock in non-interactive mode |
| DEF-M03-5 | P2 | tools/browser.py | intercept_requests ignored url_pattern |
| DEF-M07-1 | P1 | cyrax.py | Unclosed action tags silently discarded |
| DEF-M07-2 | P1 | cyrax.py | _failed_pattern_counts cross-turn pollution |
| DEF-M07-3 | P2 | cyrax.py | _turn_action_counts unbounded growth |
| DEF-M07-4 | P1 | cyrax.py | Max-depth notification silent |
| DEF-M08-1 | P1 | memory/knowledge_base.py | Severity not normalized |
| DEF-M08-2 | P1 | cyrax.py | Evidence field missing from export |
| DEF-M08-3 | P1 | cyrax.py | No JSON export |
| DEF-M08-4 | P2 | cyrax.py | Double-truncation of evidence at 100 chars |
| DEF-M09-1 | P1 | cyrax.py | Safety decisions not in audit trail |
| DEF-M09-2 | P2 | utils/safety.py | No observable policy_mode property |

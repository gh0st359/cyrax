# Gate Report: {MILESTONE} — {TITLE}

**Status:** {PASS|FAIL|BLOCKED}
**Timestamp:** {ISO-8601 timestamp}
**Branch:** {branch name}
**Commit:** {SHA}

---

## 1. Scope

{One-paragraph summary of what this milestone implemented.}

## 2. Changed Files

| File | Change Type | Notes |
|------|-------------|-------|
| path/to/file.py | added/modified/deleted | short description |

## 3. Quality Checks

| Check | Result | Evidence |
|-------|--------|----------|
| Unit tests pass for touched modules | PASS/FAIL | `pytest tests/ -m unit` — N passed |
| Integration tests pass for touched workflows | PASS/FAIL | `pytest tests/ -m integration` — N passed |
| No new flaky tests introduced | PASS/FAIL | Repeated 3× — all stable |
| Lint/type checks pass | PASS/FAIL | `flake8` / `mypy` output |
| No startup crash in smoke test | PASS/FAIL | smoke run log |
| No non-interactive hang in smoke test | PASS/FAIL | smoke run log |
| Benchmark smoke shows no regression | PASS/SKIP | benchmark output |
| Documentation updated | PASS/SKIP | files updated |

## 4. Defect Register

| ID | Severity | Description | Status | Remediation |
|----|----------|-------------|--------|-------------|
| DEF-001 | P2 | example defect | risk_accepted | tracked in follow-up |

**P0 count:** 0
**P1 count:** 0

## 5. Test Evidence

```
{pytest output summary}
```

## 6. Risks and Mitigations

- **Risk:** {description}
  **Mitigation:** {mitigation}

## 7. Go / No-Go Recommendation

**Recommendation: GO / NO-GO**

{Rationale}

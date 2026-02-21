# CYRAX Release Milestone Tracker

Last updated: 2026-02-21

| Milestone | Title | Status | Gate Report | Notes |
|-----------|-------|--------|-------------|-------|
| M00 | Release Control Plane | done | [M00.md](gates/M00.md) | Governance scaffolding |
| M01 | Deterministic Environment | pending | — | Bootstrap & preflight |
| M02 | Test and CI Hardening | pending | — | Markers, coverage, CI |
| M03 | Critical Runtime Stabilization | pending | — | P0/P1 fixes |
| M04 | Executor and Platform Robustness | pending | — | Shell hardening |
| M05 | Browser and Tool Reliability | pending | — | Preflight, fallback |
| M06 | Multi-Agent Resilience | pending | — | IPC, chaos tests |
| M07 | Orchestrator Reliability | pending | — | Loop control |
| M08 | Memory and Evidence Integrity | pending | — | Traceability, exports |
| M09 | Safety and Governance Hardening | pending | — | Policy modes, audit |
| M10 | Benchmark Harness | pending | — | Reproducible KPIs |
| M11 | Productization | pending | — | Docs, runbook |
| M12 | RC Soak and Launch Gate | pending | — | 72-hr soak, signoff |

## Status Definitions
- `pending` — not yet started
- `in_progress` — actively executing (only one allowed at a time)
- `blocked` — waiting on defect resolution or external dependency
- `done` — all exit criteria met, gate report signed GO

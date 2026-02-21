# CYRAX Release Milestone Tracker

Last updated: 2026-02-21 (M08 done)

| Milestone | Title | Status | Gate Report | Notes |
|-----------|-------|--------|-------------|-------|
| M00 | Release Control Plane | done | [M00.md](gates/M00.md) | Governance scaffolding |
| M01 | Deterministic Environment | done | [M01.md](gates/M01.md) | Bootstrap & preflight |
| M02 | Test and CI Hardening | done | [M02.md](gates/M02.md) | Markers, coverage, CI |
| M03 | Critical Runtime Stabilization | done | [M03.md](gates/M03.md) | P0/P1 fixes |
| M04 | Executor and Platform Robustness | done | [M04.md](gates/M04.md) | Shell hardening |
| M05 | Browser and Tool Reliability | done | [M05.md](gates/M05.md) | Preflight, fallback |
| M06 | Multi-Agent Resilience | done | [M06.md](gates/M06.md) | IPC, chaos tests |
| M07 | Orchestrator Reliability | done | [M07.md](gates/M07.md) | Loop control |
| M08 | Memory and Evidence Integrity | done | [M08.md](gates/M08.md) | Traceability, exports |
| M09 | Safety and Governance Hardening | in_progress | — | Policy modes, audit |
| M10 | Benchmark Harness | pending | — | Reproducible KPIs |
| M11 | Productization | pending | — | Docs, runbook |
| M12 | RC Soak and Launch Gate | pending | — | 72-hr soak, signoff |

## Status Definitions
- `pending` — not yet started
- `in_progress` — actively executing (only one allowed at a time)
- `blocked` — waiting on defect resolution or external dependency
- `done` — all exit criteria met, gate report signed GO

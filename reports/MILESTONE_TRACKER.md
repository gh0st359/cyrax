# CYRAX Release Milestone Tracker

Last updated: 2026-02-21 (M12 done — RELEASE 1.0.0)

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
| M09 | Safety and Governance Hardening | done | [M09.md](gates/M09.md) | Policy modes, audit |
| M10 | Benchmark Harness | done | [M10.md](gates/M10.md) | Reproducible KPIs |
| M11 | Productization | done | [M11.md](gates/M11.md) | Docs, runbook |
| M12 | RC Soak and Launch Gate | done | [M12.md](gates/M12.md) | 72-hr soak, signoff |

## Status Definitions
- `pending` — not yet started
- `in_progress` — actively executing (only one allowed at a time)
- `blocked` — waiting on defect resolution or external dependency
- `done` — all exit criteria met, gate report signed GO

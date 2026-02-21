# CYRAX Release Master Plan

This plan is designed for an AI coding agent to execute milestone-by-milestone with strict quality controls. It is optimized for release readiness, benchmark performance, and reliability under real offensive security workflows.

## 1. Program Objective

Build and release the most reliable, high-performing agentic red-team system in this codebase by prioritizing:

1. Stability before feature growth.
2. Deterministic behavior and reproducible benchmarking.
3. Strong safety governance with full auditability.
4. Cross-platform operability (Windows and Linux).
5. Measurable performance improvements against baseline.

## 2. Execution Rules (Mandatory)

1. Only one milestone may be `in_progress` at a time.
2. No milestone can close unless all its exit gates pass.
3. Every behavior change must include tests.
4. Every milestone must produce a gate report.
5. Any open `P0` or `P1` defect blocks progression.
6. No benchmark claims without reproducible artifact evidence.
7. Keep changes scoped to the active milestone only.

## 3. Defect Severity Policy

1. `P0`: crash, deadlock, safety bypass, data corruption.
2. `P1`: major reliability/correctness failure in key workflows.
3. `P2`: moderate defect, degraded behavior, non-critical regression.
4. `P3`: minor defect, cosmetic or low operational impact.

Gate policy:

1. `P0`: must be zero.
2. `P1`: must be zero.
3. `P2/P3`: allowed only with explicit risk note and remediation ticket.

## 4. Coding Agent Workflow Per Milestone

1. Create branch: `milestone/Mxx-short-name`.
2. Implement only the declared scope for `Mxx`.
3. Run milestone quality checks.
4. Produce gate artifacts:
   1. `reports/gates/Mxx.md`
   2. `reports/gates/Mxx.json`
5. If gates fail, fix within milestone. Do not advance.
6. Submit PR with:
   1. Scope
   2. Changed files
   3. Test evidence
   4. Risks and mitigations
   5. Go/No-Go recommendation

## 5. Global Quality Gates (Apply To Every Milestone)

1. Unit tests pass for touched modules.
2. Integration tests pass for touched workflows.
3. No new flaky tests introduced.
4. Lint/type checks pass for touched code.
5. No startup crash in smoke test.
6. No non-interactive hangs in smoke test.
7. Benchmark smoke subset shows no unacceptable regression.
8. Documentation updated for behavior changes.

## 6. Milestones

## M00 - Release Control Plane

Scope:

1. Establish release governance scaffolding and gate artifacts.

Tasks:

1. Add this master plan and milestone tracker.
2. Add gate templates and JSON schema.
3. Add gate runner script skeletons.

Quality checks:

1. Gate templates are valid and usable.
2. Milestone tracker reflects real status transitions.

Exit criteria:

1. Future milestones can be executed and audited consistently.

## M01 - Deterministic Environment

Scope:

1. Ensure predictable local and CI setup across supported platforms.

Tasks:

1. Align runtime and dev dependencies.
2. Add deterministic bootstrap scripts.
3. Add preflight checks for interpreter and toolchain.

Quality checks:

1. Fresh setup succeeds on Windows and Linux.
2. Required test tooling is always available.

Exit criteria:

1. One-command setup works reliably in clean environments.

## M02 - Test and CI Hardening

Scope:

1. Build authoritative and stable automated validation.

Tasks:

1. Add test markers (`unit`, `integration`, `e2e`, `slow`).
2. Add coverage enforcement for critical modules.
3. Add CI matrix for Windows and Linux.

Quality checks:

1. Repeated runs produce low flake rate.
2. CI is green on both platforms.

Exit criteria:

1. Automated checks are trusted release gates.

## M03 - Critical Runtime Stabilization

Scope:

1. Fix known high-impact runtime and logic defects.

Tasks:

1. Prevent startup encoding crashes on Windows consoles.
2. Correct action-count accounting to reflect true execution.
3. Fix campaign agent status/count reporting.
4. Prevent non-interactive permission deadlocks.
5. Implement missing `browser.intercept_requests` pattern behavior.

Quality checks:

1. Regression tests for each fixed defect.
2. Non-interactive smoke run does not hang.

Exit criteria:

1. No known `P0/P1` from current defect list remains.

## M04 - Executor and Platform Robustness

Scope:

1. Harden shell execution across Windows and Linux.

Tasks:

1. Improve interpreter resolution/fallback.
2. Expand path traversal and sandbox tests.
3. Harden timeout and interrupt process handling.
4. Expand Windows command adaptation coverage.

Quality checks:

1. No process leaks after interrupt stress test.
2. Unsafe path access blocked in all test variants.

Exit criteria:

1. Execution layer is predictable and safe across platforms.

## M05 - Browser and Tool Reliability

Scope:

1. Make browser/tool interactions resilient and diagnosable.

Tasks:

1. Add robust browser preflight and fallback messaging.
2. Improve browser command validation and parse failure handling.
3. Add mocked browser integration tests.
4. Improve tool availability feedback in orchestrator loop.

Quality checks:

1. Browser unavailable mode does not crash orchestration.
2. Invalid browser commands fail fast with actionable feedback.

Exit criteria:

1. Browser/tool layer is stable under degraded conditions.

## M06 - Multi-Agent Resilience

Scope:

1. Make subprocess agent orchestration robust under failures.

Tasks:

1. Harden IPC reconnect and heartbeat flows.
2. Add chaos tests for crash/disconnect/kill races.
3. Ensure no zombie subprocesses after shutdown.
4. Ensure operator status output always matches real state.

Quality checks:

1. Chaos suite passes repeatedly.
2. No stale/orphan leakage in stress runs.

Exit criteria:

1. Multi-agent runtime is production-stable.

## M07 - Orchestrator Reliability and Loop Control

Scope:

1. Reduce loop drift, hallucinated actions, and false progress.

Tasks:

1. Tighten action extraction and dispatch contract.
2. Improve no-action, dedupe, and echo regeneration handling.
3. Expand synthetic-response loop tests.
4. Ensure failure guidance is specific and non-noisy.

Quality checks:

1. Invalid-action rate decreases versus baseline.
2. Loop stall paths terminate safely and deterministically.

Exit criteria:

1. Core reasoning/action loop is reliable at scale.

## M08 - Memory and Evidence Integrity

Scope:

1. Ensure findings are trustworthy, traceable, and exportable.

Tasks:

1. Enforce evidence linkage metadata for findings.
2. Harden dedupe and consistency logic for hosts/creds/findings.
3. Add migration and schema consistency tests.
4. Improve machine-readable report completeness.

Quality checks:

1. Every finding has traceable origin fields.
2. Exported artifacts pass validation checks.

Exit criteria:

1. Evidence integrity is strong enough for public release claims.

## M09 - Safety and Governance Hardening

Scope:

1. Make safety policy deterministic and auditable by execution mode.

Tasks:

1. Add explicit policy modes (`interactive`, `auto`, `ci`).
2. Enforce strict scope-by-default in campaign mode.
3. Expand wrapped-command scope detection coverage.
4. Ensure audit logs capture decision/action/result chain.

Quality checks:

1. Out-of-scope block tests pass at 100%.
2. Non-interactive mode never prompts.

Exit criteria:

1. Safety controls are release-grade and auditable.

## M10 - Benchmark Harness and Performance Engineering

Scope:

1. Build reproducible benchmarking and optimize against objective KPIs.

Tasks:

1. Implement benchmark runner and normalized result schema.
2. Add XBOW adapter and reproducibility controls.
3. Add repeated-run variance and confidence interval reporting.
4. Add ablation suite (memory, multi-agent, prompt variants).

Quality checks:

1. Repeated runs remain within accepted variance bounds.
2. Every performance claim includes artifact evidence.

Exit criteria:

1. Benchmark framework is trustworthy and optimization-ready.

## M11 - Productization and Operator Experience

Scope:

1. Make installation, operation, and troubleshooting frictionless.

Tasks:

1. Align package metadata, runtime/dev dependencies, and docs.
2. Harmonize CLI and TUI behavior for key flows.
3. Add operator runbook and troubleshooting guide.
4. Add automated release precheck script.

Quality checks:

1. Fresh-machine install and first-run succeed on both platforms.
2. Docs validated against clean-room test.

Exit criteria:

1. Product is operable without tribal knowledge.

## M12 - RC Soak and Launch Gate

Scope:

1. Final release certification under sustained workload.

Tasks:

1. Freeze RC candidate branch and dependencies.
2. Run 72-hour soak with scripted workloads.
3. Burn down remaining defects.
4. Prepare changelog, rollback plan, and release notes.

Quality checks:

1. No open `P0`/`P1`.
2. Soak run has no critical stability failures.
3. Benchmark targets meet launch threshold.

Exit criteria:

1. Signed Go decision for public release.

## 7. Benchmark KPI Set (Must Track Every Milestone From M10 Onward)

1. Mission success rate.
2. Time to first valid action.
3. Time to first verified finding.
4. Invalid/hallucinated action rate.
5. False-positive finding rate.
6. Cost per successful mission (tokens/time).
7. Crash/hang rate per 100 sessions.
8. Scope compliance rate.

## 8. Stop Conditions (Do Not Advance)

1. Missing gate artifacts.
2. Failed mandatory checks.
3. New unresolved `P0/P1`.
4. Regression beyond accepted benchmark threshold.
5. Safety policy regression.
6. Unexplained behavior drift in orchestrator loop.

## 9. Immediate Operating Sequence

1. Execute M00 completely.
2. Execute M01 completely.
3. Execute M02 completely.
4. Execute M03 completely before any major feature expansion.
5. Continue milestone-by-milestone through M12 with strict gates.

This plan is intentionally strict. The fastest path to a dominant offensive AI system is disciplined execution with hard gates, not uncontrolled feature velocity.

# Adaptive stages

**English** · [Русский](pipeline.ru.md)

QUICK: small local change with understood dependencies. Minimal plan and normally no subagents, with implementation and relevant checks. Run independent review only when policy or assessed risk requires it. Skip separate research/design/planning/review artifacts with reasons when unnecessary.
STANDARD: feature across a few components. Targeted architecture research, design and plan (same thinking session, separate concise artifacts), implementation and checks. Optional research child only when it reduces context load; review for meaningful risk.
FULL: subsystem, broad architecture/refactoring or independent components. All stage outcomes expected, but no automatic agent per stage. Independent review normally justified. Explain any skip. Parallelism is a technical decision, not a consequence of mode alone.

Classification assesses complexity, components, architecture familiarity, design need, useful independent work, regression risk, audit need and expected change scope. Record short decisions and supporting facts, not internal deliberation. Reassess when new facts change scope; record revised assessment in plan/report without rewriting past checkpoints.

| Stage | Trigger / input | Output and completion | Skip |
|---|---|---|---|
| CLASSIFICATION | Every new task; task and targeted inspection | Eight dimensions, mode and short rationale in classification.json | Never |
| RESEARCH | Unknown patterns/dependencies; task, paths | research.md: relevant paths, conventions, dependencies, constraints, checks | Already understood/local; reason |
| DESIGN | Interface/data/architecture decisions; research | design.md: components, interfaces, data flow, tradeoffs, risks | No meaningful design decision |
| PLANNING | Coordinated changes; design/task | plan.md: ordered changes, dependencies, acceptance and check commands | Trivial local change; record minimal action in skip reason |
| IMPLEMENTATION | Classified task, plan and project rules | implementation-summary.md: actual changes/files, deviations, acceptance evidence | Never |
| TESTING | Actual implementation; available test/build/lint tools | test-report.md: exact commands, exit codes, results and gaps; successful essential checks | Never; blocked if essential checks unavailable |
| REVIEW | Successful checks plus audit need; scoped diff, acceptance | review.md: independent actionable findings or explicit no findings | Low risk/no justified audit; disclose unavailable independent context |
| FINAL_REPORT | All stages resolved | final-report.md: outcome, checks, risks, models/fallbacks, run ID | Never |

Skipped stages have a reason in their artifact or inline in state for a batch record. A batch validates all entries before writing and commits one state revision. Blocked is not skipped. Checkpoints are sequential and revision checked. State is authoritative: a report written without its state checkpoint is not committed. Resume validates actual workspace and reuses results, rather than replaying tools. State stores no conversation/context windows; Codex owns those. Do not assume resume restores memory or worktrees.

On interrupted repair, inspect `repair_iterations` and archived reports. Finish the already authorized cycle, never start a second one. Persistent failures stop the pipeline and produce a partial user report; final-report.md completion is reserved for a resolved pipeline.

# Run evidence and recovery

**English** · [Русский](journal.ru.md)

Use `python3 /.../scripts/journal.py --root PROJECT` before the operations below. Run `doctor` before starting to diagnose the current profile, settings origin, requested and observed models, delegation policy, and usage hook registration. Codex must expose its current model and native agent tools before the Skill can confirm them.

## Checks, status and resume

`check RUN --phase targeted --watch src/parser.py --watch tests/test_parser.py -- python3 -m unittest tests.test_parser` runs real argv in the project. Use the relevant project command for the next `--phase full` when a project rule or risk requires broad verification. For shell syntax, explicitly call the appropriate shell. Never put secrets into arguments. Each event contains exit code, elapsed time, log path and SHA-256 fingerprints of the watched files. Large output stays in the run folder.

Use `--reuse` only for deterministic checks when the exact inputs, environment, external dependencies and expected results match. List every relevant source, test, config and lock file with `--watch`. Unwatched state is outside the evidence. An input changed during the check is unsafe to reuse. A failed check is recorded and returns failure.

`status RUN` shows stage state, tests, observed native agent events, watched file changes, series progress and token coverage. Record confirmed native thread events with `agent RUN THREAD ROLE started|completed|failed|cancelled [--model ID]`. Use actual returned IDs. A report can include a reason when no delegation ran.

When resuming, use status first. For changed inputs after implementation, `recheck RUN` archives the prior testing/review/report and reopens those stages. It leaves source files and the existing repair count in place. Rerun affected checks and review based on the new results. Runs created by older versions have no file evidence unless checks were subsequently recorded.

## Series

Use `--series` in the Skill prompt to split comparable deliverables under one shared task. Write a short JSON list, such as:

```json
[
  {"id":"pr1","title":"Verify notebook 1"},
  {"id":"pr2","title":"Verify notebook 2"},
  {"id":"report","title":"Build the combined report","depends_on":["pr1","pr2"]}
]
```

Initialize with `series init RUN --file PATH`. Dependencies must appear earlier in the list. Give each item a `check RUN --phase targeted --item ID --watch FILE -- COMMAND ...`. Record a passing, current check using `series set RUN ID complete --check-id CHECK --summary "Verified output"`. Use `series set RUN ID blocked --summary "Reason"` for blockers. A changed item or newly verified dependency makes dependent evidence stale. Independent completed items remain reusable if their watched files are unchanged. Finish all items before completing the run.

## Optional usage

`usage enable` installs a local copy of the hook and registers only the CodeFlow handlers in project `.codex/hooks.json`. Review and trust them through Codex on a client that supports PostToolUse and PostModelRequest. The hook is disabled by default. The project hooks file contains local paths and should stay out of version control.

`usage bind RUN` requests an exact session/turn association through PostToolUse. Run it as a dedicated shell command. When actual IDs are exposed, use `--session-id` and `--turn-id` together. PostModelRequest stores only numeric counts, model ID, turn ID and an agent ID when provided. No transcript, command input or prompt is persisted by the usage hook. The counters are partial: the initial model attempt, later requests after the run closes, unsupported client events or separately identified subagents may be absent. Cached and reasoning counts are separate fields. Token counts are not subscription limits.

`usage status` reports local registration, `usage show RUN` reports the captured counts, and `usage disable` removes only this integration's registrations. Existing numeric evidence is retained.

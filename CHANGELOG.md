# Changelog

**English** · [Русский](CHANGELOG.ru.md) · [Home](README.md)

## v1.4.0 — 2026-11-21

Renamed the project and Codex plugin from CodexFlow to **CodeFlow**. The new invocation is `$codeflow:workflow`. Existing run state and settings keep their original storage paths.

Added a run journal, doctor diagnostics, opt-in local token events, watched-file checks with safe reuse, recovery of stale results, and independent series items with dependencies.

## v1.3.0 — 2026-11-21

First tagged repository snapshot. The QUICK path combines model and policy setup in `begin` and records related stages in batches. Skips can be stored inline, with reasons. Added bilingual documentation, a repository marketplace, MIT licensing and GitHub checks with tag-based packaging.

Compared plain Codex and QUICK on two local Python fixes. Both passed the original tests. See [validation](docs/validation.md) for the scope and limits of that comparison.

## Earlier builds

These versions appeared in local packages before Git history was created.

| Version | Change |
|---|---|
| 1.2.1 | Updated the Sol baseline to GPT-6.2. |
| 1.2.0 | Added efficiency, performance and speed profiles, layered configuration and saved run policy. |
| 1.1.0 | Added model generation selection from the native Codex catalog. |
| 1.0.2 | Allowed policy resolution when the client does not expose the active model ID. |
| 1.0.1 | Bundled Sol and Luna settings. |
| 1.0.0 | Introduced adaptive modes, checkpoints, resume and independent review. |

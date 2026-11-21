# Validation

**English** · [Русский](validation.ru.md) · [Home](../README.md)

## v1.3.0

Run the standard-library tests and package validator:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_package.py
```

Tests cover profile precedence, generation sorting, model permissions, hidden model identity, checkpoint ordering, locks, path validation, restart recovery and packaging. Batch checks verify that an invalid late entry does not advance an earlier entry and that failed tests can be recorded and repaired.

The documented small QUICK path went from 13 helper invocations to three. This is a count of workflow operations. It is not a token benchmark. Two paired comparisons ran ordinary Codex and QUICK on the same Python fixes with requested GPT-6.2 Sol / medium. The existing four tests passed in both arms of both tasks. Both QUICK runs reached `complete`. The first needed one extra attempt because an object was supplied where a text result was expected. The instructions were clarified. Exact token usage was unavailable.

Native plugin registration and installation were exercised locally with Codex CLI 0.170.0. GitHub Actions describes additional macOS and Linux checks and a tag build. Those hosted checks run after the repository is pushed.

## Earlier checks

- **1.2.0** exercised three profiles, saved run policy and native catalog selection. **1.2.1** verified the GPT-6.2 Sol baseline in the installed plugin.
- **1.1.0** selected GPT-6.2 Sol and GPT-6 Luna using the native catalog, without running model requests during that test.
- **1.0.x** exercised native installation and updates, interruption handling and three small Skill-driven projects. QUICK fixed arithmetic, STANDARD implemented boundary filtering, and FULL built a small FIFO queue. The fixtures passed two, three and two tests respectively. Their small scope did not exercise independent review on a large project.

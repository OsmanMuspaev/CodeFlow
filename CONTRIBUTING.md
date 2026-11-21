# Contributing

**English** · [Русский](CONTRIBUTING.ru.md) · [Home](README.md)

Issues and pull requests are welcome in either language. Include the Codex client and plugin versions, the expected behavior, and a small reproducible example. Remove private paths and data from excerpts.

Run the checks from the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_package.py
```

Update both language pages when behavior changes. Keep the English `SKILL.md` as the runtime entrypoint. Add focused regression tests for changes to model policy, state transitions, or installer behavior. Simulate catalog and model failures in tests. Use isolated projects for live Codex checks.

A pull request should describe the observable change, the tests actually run, and any remaining limits. See [releasing](docs/releasing.md) for version and tag rules.

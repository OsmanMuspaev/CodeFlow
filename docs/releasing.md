# Releasing

**English** · [Русский](releasing.ru.md) · [Home](../README.md)

Use `vMAJOR.MINOR.PATCH` tags. The tag without the leading `v` must equal the plugin version in [plugin.json](../plugins/codeflow/.codex-plugin/plugin.json). Record user-facing changes in both changelogs. Minor versions add compatible features, patch versions fix behavior, and major versions mark incompatible changes. Keep published tags immutable.

1. Update the manifest version, README badges and both changelogs. Run the unit tests and package validator.
2. Test a clean marketplace install. Build the package with `python3 scripts/prepare_marketplace.py dist/codeflow-v1.4.0` using the target version and a fresh directory.
3. Commit the verified files and create an annotated tag. Push `main` and the intended tag after reviewing the target remote.
4. The tagged GitHub Actions run tests before building a ZIP artifact. Download the archive and attach it to a draft release for that tag. Use the matching changelog entry as release notes. Verify the archive before publishing the draft.

The repository begins at `v1.3.0`. Older versions were local builds and appear in the changelog.

For first publication, create an empty GitHub repository and run these commands after replacing the remote URL:

```sh
git remote add origin https://github.com/OWNER/CodeFlow.git
git push -u origin main
git push origin v1.3.0
```

The archive contains a directory with the marketplace metadata, plugin and license. Extract it and pass the extracted marketplace directory to `codex plugin marketplace add`.

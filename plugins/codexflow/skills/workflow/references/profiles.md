# Workflow profiles and overrides

**English** · [Русский](profiles.ru.md)

| Profile | Main request | Children | Review |
|---|---|---|---|
| efficiency (default) | Sol / medium | Luna, selective | by risk |
| performance | Astra / xhigh | parallel where independent, Sol / high for review | required |
| speed | Sol / low | Luna / low, parallel where independent | by risk |

Profiles are presets. Task depth (auto, QUICK, STANDARD, FULL) is a separate setting. All profiles keep necessary tests. The current Codex session controls its actual model and reasoning. Use the client model selector when the chosen preset requests a different main model.

Settings apply in order from built-in defaults and the selected profile to user-wide settings, project settings and per-run overrides. The profile itself follows the same precedence. Custom fields continue to override a newly selected profile. Role fields merge individually. An explicit allowed_models replaces the whole allowlist, so custom role IDs must also be in that map. Old complete project configs retain exact model IDs unless they opt in to auto. Use a minimal override file to keep inheriting future defaults.

- User-wide settings are in `$CODEX_HOME/codexflow/config.json` (normally `~/.codex/codexflow/config.json`). They apply to CodexFlow in every project.
- Project settings are in `.codexflow/config.json`.
- A run override is stored in the effective policy in state.json. Resume reuses that policy.

Adjust `default_mode`, `model_selection` (auto/pinned), `allowed_models`, `allow_premium`, `delegation` (off/selective/parallel), `max_subagents` (1 or 2), `review_policy` (risk/always/off), `soft_token_budget`, `report_words`, `handoff_words`, and each role's model, reasoning_effort and fallback separately. Mandatory independent review cannot be combined with delegation off. A soft budget is guidance where usage is exposed.

Examples in Codex:

```text
$codexflow:workflow --profile speed --set max_subagents=1 Обнови тесты
$codexflow:workflow config --global --save --profile efficiency
$codexflow:workflow config --save --profile performance --set max_subagents=1
$codexflow:workflow config --save --set roles.review_model.reasoning_effort="high"
```

The helper accepts `--profile` and repeated `--set` before its subcommand, for example `flow.py --profile speed --set max_subagents=1 config --save`. When calling a shell, quote the complete key=JSON argument to retain JSON string quotes. Inspect with `config` or `config --global`. Only explicit `--init` or `--save` writes a persistent config.

# Compatibility research

**English** · [Русский](research.ru.md) · [Home](../README.md)

The initial design was researched on 2026-11-17 with Codex CLI 0.169.0. Subsequent behavior is described in [Architecture](architecture.md).

- [Skills](https://learn.chatgpt.com/docs/build-skills) describes the `SKILL.md` format and explicit Skill invocation.
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) describes agent threads, model inheritance and client-specific controls.
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins) describes the Codex compatibility manifest and marketplace paths.
- [Plugins](https://learn.chatgpt.com/docs/plugins) describes client support.

CodexFlow packages a Skill with the Codex compatibility manifest. Role documents describe handoffs. They do not register custom agent types. The Python helper owns local state and policy validation. Codex owns the workflow, model session and native agent threads.

Skills have no portable mechanism for switching the parent model, reading prices and quotas, enforcing token budgets or assigning a separate child sandbox. The current release checks the evidence the client actually exposes. Premium models require explicit configuration. The original implementation excluded them entirely.

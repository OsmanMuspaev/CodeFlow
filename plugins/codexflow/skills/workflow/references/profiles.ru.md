# Профили и переопределения

[English](profiles.md) · **Русский**

| Профиль | Главная роль | Субагенты | Ревью |
|---|---|---|---|
| `efficiency` по умолчанию | Sol / medium | Luna по необходимости | По риску |
| `performance` | Astra / xhigh | Независимые задачи параллельно | Обязательное, Sol / high |
| `speed` | Sol / low | Luna / low, независимые задачи параллельно | По риску |

Глубина auto, QUICK, STANDARD или FULL задаётся отдельно. Проверки проекта нужны при любом профиле. Фактической моделью и reasoning главной сессии управляет клиент.

Приоритет от меньшего к большему — встроенный профиль, общие настройки, проект и параметры запуска. Поля ролей объединяются по отдельности. Явный `allowed_models` заменяет весь список. Поля сохранённого конфига сохраняют приоритет над профилем. Старые полные конфиги сохраняют точные ID, пока пользователь не включит автоматический подбор. Запуск хранит действовавшие настройки для `resume`.

- Общий файл — `$CODEX_HOME/codexflow/config.json`, обычно `~/.codex/codexflow/config.json`.
- Проектный файл — `.codexflow/config.json`.
- Только явные `config --init` или `config --save` записывают конфигурацию.

Отдельно задаются `default_mode`, `model_selection` (auto/pinned), `allowed_models`, `allow_premium`, `delegation` (off/selective/parallel), `max_subagents` (1 или 2), `review_policy` (risk/always/off), `soft_token_budget`, `report_words`, `handoff_words`, а также `model`, `reasoning_effort` и `fallback` каждой роли. Обязательное независимое ревью требует делегирования. Бюджет учитывается только при доступной статистике.

```text
$codexflow:workflow --profile speed --set max_subagents=1 Обнови тесты
$codexflow:workflow config --global --save --profile efficiency
$codexflow:workflow config --save --profile performance
$codexflow:workflow config --save --set roles.review_model.reasoning_effort="high"
```

При вызове Python-скрипта флаги `--profile` и `--set` идут перед подкомандой. В оболочке заключайте всё выражение `ключ=JSON` в кавычки, чтобы сохранить кавычки JSON-строки.

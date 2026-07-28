---
name: api-usage-monitoring
description: Use when Rui wants Hermes to inspect connected LLM/API providers for balance, credits, usage windows, rate-limit/quota state, or to extend the local API Usage Monitor plugin with new provider adapters.
version: 0.2.0
author: Rui Valim
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [api-usage, credits, quotas, providers, desktop-plugin, monitoring, subscriptions]
    category: productivity
    related_skills: [hermes-agent, hermes-desktop-plugins]
---

# API Usage Monitoring

Provider-agnostic API Usage Monitor — standalone app + Hermes plugin. Checks balances, usage windows, quotas, rate-limits for subscriptions (Claude, Kimi) and credits (DeepSeek, Codex, Nous) across configured providers.

## CLI Usage

```bash
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py status
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py status --json --pretty
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py status --notify --snooze-seconds 300
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py latest --limit 3 --pretty
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py serve --port 9097 --refresh-interval 900
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py menubar --interval 300
~/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py autostart --output-dir /tmp/usage-monitor-agents
```

Uses Hermes venv Python. Appends snapshots to `~/.config/usagemon/snapshots.jsonl`.

Repo Makefile shortcuts:

```bash
make setup
make tray              # foreground menu bar app
make install-tray      # install+load backend + tray LaunchAgents
make uninstall-tray
make status-tray
make logs
```

## Standalone config

Provider registry lives at `~/.config/usagemon/providers.yaml` (sample in repo `examples/providers.yaml`). Prefer macOS Keychain credentials:

```bash
security add-generic-password -a default -s api-usage-monitor/deepseek -w 'sk-...'
```

Supported config types: `deepseek`, `kimi`, `openai-compatible`, `generic-http`, `placeholder`, `hermes-account-usage`, `anthropic-subscription`, `hermes-auth`, `hermes-nous`, `hermes-state-db`.

External price table: `~/.config/usagemon/prices.yaml` / `USAGE_MONITOR_PRICES_FILE`.

Alerts: `~/.config/usagemon/alert_state.json`, `USAGE_MONITOR_ALERT_SNOOZE_SECONDS`, `USAGE_MONITOR_ALERT_NOTIFY=0` to suppress real osascript notifications.

Menu bar app is optional and requires `rumps` (`pip install 'usage-monitor[menubar]'`); without it, only the menubar command exits 2 with a clear message. `usagectl autostart` only prints/writes LaunchAgent plists; it never runs `launchctl`.

## Providers

| Provider | Source | Type |
|----------|--------|------|
| Claude / Anthropic | `claude -p "/usage"` CLI + OAuth account_usage | Subscription |
| Kimi Coding | OAuth `api.kimi.com/coding/v1/usages` | Subscription |
| OpenAI Codex | Hermes account_usage | Subscription |
| DeepSeek | `/user/balance` API | Credits |
| Nous Portal | Portal credit lines | Credits |
| Z.ai / GLM | Credential pool placeholder | Credits |
| Hermes auth | `hermes auth list` | Auth state |
| Hermes state.db | `session_model_usage` SQLite read-only (`mode=ro`) | Recorded usage |

## REST API

Mounted under `/api/plugins/api-usage-monitor`:

| Route | Method | Purpose |
|---|---|---|
| `/status` | GET | Current status |
| `/refresh` | POST | Status + persist snapshot |
| `/latest?limit=N` | GET | Latest snapshots |
| `/history?limit=N` | GET | History for dashboard charts |
| `/status.txt` | GET | Text |
| `/status.json?pretty=true` | GET | JSON |
| `/` | GET | Static dashboard |

## External Adapters

Drop `.py` files in `~/.config/usagemon/adapters/` with a `check()` function returning a dict/ProviderStatus/list. Ship adapters: `claude_cli.py`, `kimi_cli.py`.

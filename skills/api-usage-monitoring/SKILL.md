---
name: api-usage-monitoring
description: Use when inspecting connected LLM/API providers for balance, credits, usage windows, rate-limit/quota state, or to extend the local API Usage Monitor with new provider adapters.
version: 0.2.0
author: Rui Valim
license: MIT
platforms: [macos, linux]
metadata:
  tags: [api-usage, credits, quotas, providers, monitoring, subscriptions]
  category: productivity
---

# API Usage Monitoring

Provider-agnostic API Usage Monitor — a standalone app that runs on its own. Checks balances, usage windows, quotas, rate-limits for subscriptions (Claude, Kimi) and credits (DeepSeek, Codex, Nous) across configured providers.

## CLI Usage

`usage-monitor` is the public standalone CLI installed by the Python package. `usagemon` remains as a backwards-compatible alias. `usagemon` is the repo-pinned wrapper installed by `make install-cli`; it runs the repo's own venv. Equivalent to `<repo>/.venv/bin/python <repo>/scripts/usagectl.py`.

```bash
usage-monitor status
usage-monitor status --json --pretty
usage-monitor status --notify --snooze-seconds 300
usage-monitor latest --limit 3 --pretty
usage-monitor serve --port 9097 --refresh-interval 900
usage-monitor menubar --interval 300
usage-monitor autostart --output-dir /tmp/usage-monitor-agents
```

Appends snapshots to `~/.config/usagemon/snapshots.jsonl`.

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

The `hermes-*` types (plus `anthropic-subscription`) are an optional integration: without a Hermes install they report `unavailable` and everything else keeps working. Install the optional skill/plugin bridge with `scripts/install-hermes-integration.sh`.

External price table: `~/.config/usagemon/prices.yaml` / `USAGE_MONITOR_PRICES_FILE`.

Alerts: `~/.config/usagemon/alert_state.json`, `USAGE_MONITOR_ALERT_SNOOZE_SECONDS`, `USAGE_MONITOR_ALERT_NOTIFY=0` to suppress real osascript notifications.

Menu bar app is optional and requires `rumps` (`pip install 'usage-monitor[menubar]'`); without it, only the menubar command exits 2 with a clear message. `usagemon autostart` only prints/writes LaunchAgent plists; it never runs `launchctl`.

## Providers

| Provider | Source | Type |
|----------|--------|------|
| Claude / Anthropic | `claude -p "/usage"` CLI + OAuth account_usage | Subscription |
| Kimi Coding | OAuth `api.kimi.com/coding/v1/usages` | Subscription |
| OpenAI Codex | Hermes `account_usage` (optional) | Subscription |
| DeepSeek | `/user/balance` API | Credits |
| Nous Portal | Portal credit lines | Credits |
| Z.ai / GLM | Credential pool placeholder | Credits |
| Hermes auth | `hermes auth list` (optional) | Auth state |
| Hermes state.db | `session_model_usage` SQLite read-only, `mode=ro` (optional) | Recorded usage |

## REST API

Served by the standalone app; also mounted under `/api/plugins/api-usage-monitor` when the optional Hermes plugin is installed:

| Route | Method | Purpose |
|---|---|---|
| `/status` | GET | Current status |
| `/refresh` | POST | Status + persist snapshot |
| `/latest?limit=N` | GET | Latest snapshots |
| `/history?limit=N` | GET | History for dashboard charts |
| `/status.txt` | GET | Text |
| `/status.json?pretty=true` | GET | JSON |
| `/` | GET | Static dashboard |

## Hermes Desktop plugin (optional)

`plugin/desktop/plugin.js` registers a status bar chip, a right-hand pane with the provider table, and an `API Usage: Refresh` palette command. It reads the plugin's own `/status` and `/refresh` routes, so it needs the plugin backend installed. `scripts/install-hermes-integration.sh` copies it to `~/.hermes/desktop-plugins/api-usage-monitor/plugin.js`.

## External Adapters

Drop `.py` files in `~/.config/usagemon/adapters/` with a `check()` function returning a dict/ProviderStatus/list. Ship adapters: `claude_cli.py`, `kimi_cli.py`.

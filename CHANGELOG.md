# Changelog

All notable changes to this project are documented here.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are project/package versions; release tags are created only when explicitly requested.

## [Unreleased]

### Added

- OpenCode Go subscription usage windows (`opencode-go` / `opencode`) via `opencode.ai/zen/go/v1/usage`.
- Charm Hyper credits balance (`charm-hyper` / `hyper`) via `hyper.charm.land/v1/credits`.
- Credential source `file` (token in a `0600` file), for Linux/systemd (`LoadCredential`) setups.

### Added

- SuperGrok weekly usage pool (`supergrok` / `grok`) via Grok CLI session (`~/.grok/auth.json`).
- xAI Management API prepaid credits (`xai`).
- OpenAI org costs (`openai`) via Admin key; honest error for secret-key `credit_grants`.
- OpenAI Codex / ChatGPT subscription (`codex`): `/wham/usage` with device-code OAuth (`usagemon codex-login` → Keychain) or `~/.codex/auth.json`.
- Display `name` on provider entries (multi-sub of the same `type` with unique `id`).
- Explicit file-adapter types: `claude-cli`, `kimi-cli`, `qwen-token-plan`, `antigravity` (YAML only).

### Changed

- Collect is **YAML-only**: `adapters/*.py` are no longer auto-loaded.
- Provider entry `name` overrides display label for multi-account setups.

### Removed

- `hermes-auth` provider type and all **show auth** UI (dashboard, menubar, plugin).
- Z.ai placeholder and OpenAI `credit_grants` recommendations from examples.

- Public CLI entry point `usage-monitor` while keeping `usagectl` as a backwards-compatible alias.
- SPDX MIT headers across Python source files.
- Standalone Python package `usage_monitor_app`.
- Config-driven provider registry loaded from `~/.hermes/usage/providers.yaml`.
- Credential sources for Keychain, env vars, literal test values, and optional Hermes credential pool.
- Generic `openai-compatible` / `generic-http` provider check.
- Standalone FastAPI app with static dashboard.
- App-owned refresh scheduler via `usagectl serve --refresh-interval` or `USAGE_MONITOR_REFRESH_INTERVAL`.
- `/history` endpoint backed by `snapshots.jsonl`.
- Example configs: `examples/providers.yaml` and `examples/prices.yaml`.
- Packaging metadata: `pyproject.toml`, `setup.py`, and `usagectl` entrypoint.
- Documentation under `docs/` for usage, configuration, extension, architecture, and roadmap.
- Pytest harness covering config loading, provider/window coercion, registry dispatch, snapshot persistence, HTTP status mapping, FastAPI endpoints, and built-in adapters with fake payloads.
- Dev extra `.[dev]` with pytest and pytest testpath config.
- Read-only Hermes `state.db` adapter (`hermes-state-db`) aggregating recorded usage by provider/model from configured SQLite DB paths/profiles.
- Pure cost-estimation module `usage_monitor_app/costs.py`: documented `prices.yaml` v1 schema (per-provider currency/source metadata, per-model input/output/cached prices per 1M tokens), model-name normalization, single-currency `CostEstimate` results with graceful `None` on missing provider/model/prices, and currency-keyed `sum_estimates` aggregation.
- `tests/test_costs.py` covering cost estimation with fake price tables.
- `tests/test_hermes_state_db.py` covering state DB aggregation, schema drift, profile filtering, read-only access, and price-table estimate details.
- Alert module `usage_monitor_app/alerts.py`: dedup/snooze state in `alert_state.json` (configurable via `USAGE_MONITOR_ALERT_*` env vars), macOS `osascript display notification` delivery with injectable command runner, wired into the standalone scheduler after each persisted snapshot; core `collect_status` stays pure.
- `usagectl status --notify [--snooze-seconds N]` for manual alert notification.
- `tests/test_alerts.py` covering dedup/snooze computation, state persistence, and notification command building with fake snapshots and a fake runner.
- Optional macOS menu bar app `usage_monitor_app/menubar.py` (extra `.[menubar]`, requires `rumps`): menu bar icon with overall status, one menu line per provider, `Refresh Now`, `Open Dashboard`, and `Quit` actions, plus optional auto-refresh timer. Calls `core.collect_status` directly and never manages the FastAPI backend; `rumps`, the collect callable, and `webbrowser` are injectable. Without `rumps` installed, `usagectl menubar` prints a clear install message and exits 2.
- `usagectl menubar [--interval N] [--dashboard-url URL] [--no-persist]` CLI entrypoint, with `USAGE_MONITOR_MENUBAR_INTERVAL` / `USAGE_MONITOR_DASHBOARD_URL` env defaults.
- `tests/test_menubar.py` covering menu/title rendering, refresh/open-dashboard actions, refresh-failure handling, timer behavior, and missing-`rumps` behavior with fully faked `rumps`/`webbrowser` modules (no real GUI).
- LaunchAgent autostart generation `usage_monitor_app/autostart.py`: explicit `AutostartConfig` (python executable, working dir, usagectl path, host/port, refresh interval, log dir, dashboard URL, menubar interval, label prefix), plist dicts serialized via `plistlib` (always valid XML), server plist restarts on crash, menubar plist marked `ProcessType: Interactive`. Generation-only: never calls `launchctl`, never loads/enables agents.
- `usagectl autostart [--kind server|menubar|both] [--output-dir DIR]` to print plist XML to stdout or write `*.plist` files into a chosen directory, with manual `launchctl bootstrap` hints printed after writing.
- Example LaunchAgent templates with placeholder paths under `templates/launchagents/`; the installer copies them to `~/.hermes/usage/launchagents/` for reference only (nothing is loaded or enabled).
- Makefile shortcuts for setup, tests, foreground server/tray, LaunchAgent generation, tray install/uninstall/restart/status, and logs.
- `tests/test_autostart.py` covering plist XML round-trip validation via `plistlib`, config-to-plist mapping, file writing into temp dirs, CLI stdout/output-dir behavior, and validity of the packaged template examples.

### Changed

- `plugin/usage_monitor.py` is now a backwards-compatible shim over `usage_monitor_app.core`.
- Hermes-specific integrations now import `agent.*` lazily inside optional adapters instead of during core import.
- Hermes plugin API now serves the dashboard at `/api/plugins/api-usage-monitor/` and history at `/api/plugins/api-usage-monitor/history`.
- Installer copies the standalone package into the Hermes plugin dashboard directory.
- Installer writes `.example.yaml` config samples and does not overwrite real user config.
- CLI wrapper can run from repo, from Hermes install, or as installed package.

### Known gaps

- No native macOS menu bar/tray icon yet.
- Price-table estimates are not yet surfaced broadly in dashboard summaries beyond `hermes-state-db` details.
- Hermes profile `state.db` auto-discovery is not implemented yet; DB paths are configured manually.
- Alert thresholds are not configurable in `providers.yaml` yet; only global dedup/snooze exists.
- Kimi usage endpoint remains unresolved for non-interactive usage checks.

## [0.2.0] - 2026-07-25

### Added

- Initial Hermes API Usage Monitor plugin.
- Provider status dataclasses and JSON snapshot format.
- OpenRouter, DeepSeek, Kimi, Z.ai placeholder, Codex, Nous, and Hermes auth checks.
- External adapter loading from `~/.hermes/usage/adapters/`.
- CLI adapters for Claude, Grok, and Kimi.
- Hermes plugin REST API for status, refresh, latest snapshots, text, and JSON.
- Installer and uninstaller.
- Initial `api-usage-monitoring` Hermes skill.

### Removed

- Anthropic API adapter after Claude CLI/Hermes account-usage path covered the subscription check.

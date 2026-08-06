# Architecture

The project is now split into a standalone package plus Hermes compatibility shims.

## Modules

```text
usage_monitor_app/
  core.py        # dataclasses, config loader, registry, built-in adapters, snapshots
  costs.py       # pure price-table loading and token-cost estimation (no network)
  alerts.py      # alert dedup/snooze state + macOS osascript notifications (injectable runner)
  menubar.py     # optional rumps menu bar app (imports rumps lazily; fully injectable for tests)
  autostart.py   # LaunchAgent plist generation (pure file generation; never calls launchctl)
  web.py         # standalone FastAPI app, dashboard, scheduler
  __init__.py    # public package exports

plugin/
  plugin_api.py      # Hermes plugin APIRouter, uses usage_monitor_app
  usage_monitor.py   # backwards-compatible shim for old imports/adapters
  adapters/          # shipped external CLI adapters copied to ~/.config/usagemon/adapters

scripts/
  usagectl.py    # CLI wrapper: status/latest/menubar/serve/autostart

templates/
  launchagents/  # example LaunchAgent plists with placeholder paths (reference only)

examples/
  providers.yaml # provider registry sample
  prices.yaml    # external price table sample
```

## Core principles

- The core package must be importable without Hermes installed.
- Hermes-specific imports stay lazy and optional.
- Provider checks return one generic `ProviderStatus` shape.
- Snapshots are append-only JSONL.
- Credentials should come from Keychain/env/config/Hermes pool, not hardcoded files.

## Data flow

```text
providers.yaml
  -> config loader
  -> provider registry
  -> adapter checks
  -> ProviderStatus[]
  -> MonitorSnapshot
  -> snapshots.jsonl
  -> CLI / FastAPI / dashboard
```

External adapters are loaded after config-driven providers:

```text
~/.config/usagemon/adapters/*.py
```

## Optional Hermes bridge

These provider types import Hermes lazily:

- `hermes-account-usage`
- `anthropic-subscription`
- `hermes-auth`
- `hermes-nous`
- `hermes-state-db` (SQLite read-only; no Hermes Python import)
- `credential.source: hermes`

If Hermes is not available, the related provider becomes `unavailable`; the app itself should keep running.

## Standalone web app

`usage_monitor_app.web:create_app()` exposes:

- static HTML dashboard;
- status endpoints;
- history endpoint;
- optional scheduler via `USAGE_MONITOR_REFRESH_INTERVAL`.

The scheduler is intentionally simple: one daemon thread calls `collect_status(persist=True)` every interval, then passes the snapshot to `usage_monitor_app/alerts.py`, which dedups/snoozes alerts via `alert_state.json` and sends new ones as macOS `osascript` notifications. Core `collect_status` never notifies.

## Hermes plugin app

`plugin/plugin_api.py` mounts the same functionality under Hermes Desktop/Gateway. It does not own scheduling; standalone mode does.

## Menu bar app

`usage_monitor_app/menubar.py` is an optional `rumps`-based macOS menu bar app (`usagemon menubar`, extra `.[menubar]`). It renders the overall status icon plus one line per provider, and offers `Refresh Now`, `Open Dashboard`, `Show auth`, `Show inactive`, and `Quit`. Providers with nothing to report — status `unavailable` or `unknown`, or muted via `relevant: false` — are hidden behind `Show inactive`, with a trailing count of what was hidden. It calls `core.collect_status` directly — it does not start, detect, or manage the FastAPI backend; `Open Dashboard` only opens the configured URL in the browser. `rumps` is imported lazily inside the entrypoint, so the module (and the rest of the package) stays importable without it; the `rumps` module, the collect callable, and `webbrowser` are all injectable so tests run headless with fakes.

## Autostart (LaunchAgents)

`usage_monitor_app/autostart.py` builds launchd plist dicts for the standalone backend (`usagemon serve`) and the menu bar app (`usagemon menubar`), serializing them with `plistlib` so the output is always valid plist XML. Every path is an explicit `AutostartConfig` field: python executable, working directory, `usagectl.py` path, host/port, refresh interval, log directory, dashboard URL, menubar interval, and label prefix. `usagemon autostart` prints the XML to stdout or writes `*.plist` files into a caller-chosen `--output-dir`; it never runs `launchctl` and never loads/enables agents. Example templates with placeholder paths live in `templates/launchagents/` and are copied (not activated) by the installer. The server plist restarts on crash (`KeepAlive: {SuccessfulExit: false}`); the menubar plist is `ProcessType: Interactive` because `rumps` needs the GUI session.

## Known gaps

- No backend lifecycle management for the menu bar app (detect/reuse/launch the local FastAPI server) yet; autostart is generation-only and activation is manual.
- Price-table cost estimation exists as a pure library (`usage_monitor_app/costs.py`) and is used by `hermes-state-db` details, but is not yet surfaced broadly in snapshots/CLI/dashboard summaries.
- Hermes profile auto-discovery is not implemented yet; `hermes-state-db` currently uses configured `path`/`db_paths`.
- Alert thresholds are not configurable in `providers.yaml` yet; dedup/snooze exists but per-provider thresholds do not.
- Kimi HTTP usage endpoint is still unresolved; CLI `/usage` remains interactive-only.

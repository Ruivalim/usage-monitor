# Usage

`usage-monitor` can run standalone first:

1. standalone local app (`usagectl serve`), useful outside Hermes;
2. optional Hermes plugin backend mounted under `/api/plugins/api-usage-monitor`.

## Terminal commands

From the repo:

```bash
cd ~/work/ruivalim/usage-monitor
python3.11 scripts/usagectl.py status
python3.11 scripts/usagectl.py status --json --pretty
python3.11 scripts/usagectl.py latest --limit 5 --pretty
```

Installed editable:

```bash
python3.11 -m pip install -e .
usagectl status
usagectl status --json --pretty
usagectl latest --limit 5 --pretty
```

## Standalone dashboard

```bash
usagectl serve --port 9097
```

Open:

```text
http://127.0.0.1:9097
```

`config.yaml` controls the app-owned scheduler (`intervals.refresh_seconds`, default 900s). Override with `--refresh-interval N`; use `0` to disable background refresh. Each scheduler tick also processes alerts: new (deduped) alerts are sent as macOS notifications — see `docs/CONFIGURATION.md` for the `USAGE_MONITOR_ALERT_*` knobs.

## macOS menu bar app (optional)

Requires the optional `rumps` dependency:

```bash
make setup
make tray                             # foreground tray, Ctrl+C stops

# manual equivalent:
python3.11 -m pip install -e '.[menubar]'
usagectl menubar                      # auto-refresh from config.yaml (default 300s)
usagectl menubar --interval 900
usagectl menubar --no-persist         # refresh without appending snapshots
```

The app shows the overall status icon in the menu bar (🟢 ok / 🟡 warning / 🔴 error / ⚪ unknown), one menu line per provider, and `Refresh Now`, `Open Dashboard`, `Show auth`, startup, and `Quit` actions. Language is controlled by `dashboard.language` (`en`/`pt`) or `--language`.

It is a view over `core.collect_status` only: it does not start or manage the FastAPI backend. `Open Dashboard` opens the configured dashboard URL in the default browser (`--dashboard-url` or `USAGE_MONITOR_DASHBOARD_URL`, default `http://127.0.0.1:9097`) — start the backend separately with `usagectl serve` if you want the dashboard live.

Without `rumps` installed, `usagectl menubar` prints a clear install message and exits with code 2; every other command keeps working.

## macOS autostart (LaunchAgents)

`usagectl autostart` generates LaunchAgent plist files for the standalone
FastAPI backend (`usagectl serve`) and optionally the menu bar app
(`usagectl menubar`). It is generation-only: it never runs `launchctl`,
never loads/enables/starts anything, and writes only into an output
directory you pass explicitly (or stdout).

Makefile shortcuts:

```bash
make autostart-generate               # writes .launchagents/*.plist
make install-tray                     # launchctl bootstrap server + menubar
make status-tray                      # launchctl print both labels
make logs                             # tail ~/.config/usagemon/logs/*.log
make uninstall-tray                   # bootout + remove copied plists
```

`make install-tray` installs both agents because the tray is useful alone for
status, but `Open Dashboard` needs the FastAPI backend running.

```bash
# print plist XML to stdout
usagectl autostart --kind server
usagectl autostart --kind menubar
usagectl autostart                      # both documents

# write plist files into a chosen directory
usagectl autostart --output-dir /tmp/usage-monitor-agents --kind both
```

All paths are explicit and configurable:

| Flag | Default | Purpose |
|---|---|---|
| `--kind` | `both` | `server`, `menubar`, or `both` |
| `--output-dir` | stdout | Directory to write `*.plist` files into |
| `--python` | current interpreter | Python executable used by the agent |
| `--working-dir` | repo root | `WorkingDirectory` of the agent |
| `--usagectl` | `<working-dir>/scripts/usagectl.py` | CLI entrypoint executed by the agent |
| `--host` / `--port` | `127.0.0.1` / `9097` | Backend bind address |
| `--refresh-interval` | `900` | Backend scheduler seconds; `0` disables |
| `--log-dir` | `$USAGE_MONITOR_HOME/logs` | stdout/stderr log location |
| `--dashboard-url` | `http://<host>:<port>` | URL the menu bar app opens |
| `--menubar-interval` | `300` | Menu bar auto-refresh seconds; `0` disables |
| `--label-prefix` | `com.usage-monitor` | launchd label prefix |

Activating an agent is always a separate, manual step:

```bash
mkdir -p ~/.config/usagemon/logs
cp com.usage-monitor.server.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.usage-monitor.server.plist
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.usage-monitor.server.plist  # stop
```

Example templates with placeholder paths live in `templates/launchagents/`
(the optional Hermes installer copies them to `~/.hermes/usage/launchagents/`
for reference only). The menubar plist sets `ProcessType: Interactive` because `rumps`
needs the GUI session; the server plist restarts on crash
(`KeepAlive: {SuccessfulExit: false}`).

## Alert notifications

```bash
usagectl status --notify                     # notify for new alerts only (dedup/snooze state)
usagectl status --notify --snooze-seconds 300
```

Dedup/snooze state lives in `~/.config/usagemon/alert_state.json` (override with `USAGE_MONITOR_ALERT_STATE_FILE`).

## HTTP endpoints

The local API is served twice: under `/api/v1` (stable, versioned, what external
tools should use) and unprefixed (the historical paths, kept for backwards
compatibility and used by the dashboard's relative fetches). Both prefixes are
served by the same handlers, so behavior can never drift between them.

Everything binds to `127.0.0.1` by default. Basic Auth is optional and protects
the dashboard, `/docs`, `/openapi.json`, and every data route; enable it with
`usagectl auth enable` or environment variables (`USAGE_MONITOR_AUTH_PASSWORD`,
`USAGE_MONITOR_AUTH_PASSWORD_HASH`).

| Route | Method | Purpose |
|---|---:|---|
| `/api/v1/status` | GET | Current status. `?persist=true` also appends a snapshot (default `false`). |
| `/api/v1/status/cached` | GET | Newest persisted snapshot; collects without persisting only if none exists. |
| `/api/v1/refresh` | POST | Collect and persist a snapshot. |
| `/api/v1/latest?limit=N` | GET | Latest persisted snapshots (`limit` 1–500, default 50). |
| `/api/v1/history?limit=N` | GET | Same payload as `/latest`, named for chart consumers. |
| `/api/v1/status.txt` | GET | Plain-text status. |
| `/api/v1/status.json?pretty=true` | GET | JSON served as text. |
| `/api/v1/overrides` | GET | Per-provider overrides (`{"<id>": {"relevant": false}}`). |
| `/api/v1/overrides/{provider_id}` | POST | Set relevance: `{"relevant": true|false}`. |
| `/api/v1/autostart` | GET | LaunchAgent state. |
| `/api/v1/autostart/install` | POST | Install/reload LaunchAgents. |
| `/api/v1/autostart/uninstall` | POST | Stop the menubar agent and remove both plists. |

```bash
curl -s http://127.0.0.1:9097/api/v1/status | jq .overall
curl -s http://127.0.0.1:9097/api/v1/status.txt
curl -sX POST http://127.0.0.1:9097/api/v1/refresh > /dev/null
curl -sX POST http://127.0.0.1:9097/api/v1/overrides/nous \
  -H 'Content-Type: application/json' -d '{"relevant": false}'
```

The full schema is browsable at `/docs` (Swagger UI) and `/openapi.json`.

Backwards-compatible unprefixed routes:

| Route | Method | Purpose |
|---|---:|---|
| `/` | GET | Static dashboard |
| `/status` | GET | Current status. Add `?persist=true` to append a snapshot. |
| `/status/cached` | GET | Fast path: newest persisted snapshot (collects only if none exists) |
| `/refresh` | POST | Current status + persisted snapshot |
| `/latest?limit=N` | GET | Latest persisted snapshots |
| `/history?limit=N` | GET | Snapshot history for charts |
| `/status.txt` | GET | Plain-text status |
| `/status.json?pretty=true` | GET | JSON as text |
| `/overrides` | GET | Per-provider overrides (`{"<id>": {"relevant": false}}`) |
| `/overrides/{provider_id}` | POST | Set relevance: `{"relevant": true|false}`. Non-relevant providers are shown muted and excluded from `overall`/alerts. Stored in `$USAGE_MONITOR_HOME/overrides.json`. |
| `/autostart` | GET | LaunchAgent state (plist files + `launchctl` loaded/pid) |
| `/autostart/install` | POST | Write both plists into `~/Library/LaunchAgents` and (re)load the menubar agent. The running server is never re-bootstrapped (its plist takes effect at next login). |
| `/autostart/uninstall` | POST | Stop the menubar agent and remove both plists. The running server stays alive until logout. |

The Hermes plugin backend exposes a subset of the same routes under:

```text
/api/plugins/api-usage-monitor
```

## Snapshot storage

Default location:

```text
~/.config/usagemon/snapshots.jsonl
```

Override with:

```bash
export USAGE_MONITOR_HOME=/path/to/state
export USAGE_MONITOR_SNAPSHOT_FILE=/path/to/snapshots.jsonl
```

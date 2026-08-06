# LaunchAgent examples (macOS autostart)

These two plist files are **examples only**, generated from placeholder paths
(`/path/to/python3`, `/path/to/usage-monitor`, `/path/to/state/logs`).
Nothing here is loaded or enabled by the installer.

Do **not** edit these by hand for real use. Generate plists with your actual
paths instead:

```bash
# print to stdout
usagemon autostart --kind server
usagemon autostart --kind menubar

# write into a directory of your choice (never ~/Library/LaunchAgents unless you say so)
usagemon autostart --output-dir ~/Desktop/usage-monitor-agents \
  --python /usr/bin/python3 \
  --working-dir ~/work/usage-monitor \
  --port 9097 --refresh-interval 900 \
  --log-dir ~/.hermes/usage/logs \
  --dashboard-url http://127.0.0.1:9097 \
  --menubar-interval 300
```

The generator only writes files. To actually activate an agent, copy it and
bootstrap it yourself:

```bash
cp com.usage-monitor.server.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.usage-monitor.server.plist

# stop later with:
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.usage-monitor.server.plist
```

- `com.usage-monitor.server.plist` — runs `usagemon serve` (FastAPI
  backend + dashboard), restarts on crash.
- `com.usage-monitor.menubar.plist` — runs `usagemon menubar`
  (optional rumps menu bar app; requires the `.[menubar]` extra), marked
  `ProcessType: Interactive` because it needs the GUI session.

# Roadmap

Goal: finish `usage-monitor` as a standalone local usage/credits cockpit while keeping Hermes plugin compatibility.

## Phase 1 — Standalone foundation

Status: mostly done.

- [x] Extract core into importable package.
- [x] Remove Hermes `sys.path` mutation from core import.
- [x] Make Hermes adapters optional/lazy.
- [x] Add config-driven provider registry.
- [x] Add Keychain/env/Hermes credential sources.
- [x] Add standalone FastAPI app.
- [x] Add static dashboard.
- [x] Add scheduler-owned refresh.
- [x] Add history endpoint from `snapshots.jsonl`.
- [x] Add tests around config loading, registry dispatch, and snapshot persistence.
- [x] Add FastAPI endpoint tests and adapter tests with fake payloads.

## Phase 2 — macOS app surface

Recommended next implementation: Python `rumps` menu bar app.

Current status: `usage_monitor_app/menubar.py` (`usagemon menubar`, extra `.[menubar]`) covers the icon, per-provider lines, refresh, and open-dashboard actions. Remaining: backend lifecycle and autostart.

- [x] Menu bar/tray icon showing overall status.
- [x] Menu items for each provider summary.
- [x] Manual `Refresh` action.
- [x] `Open Dashboard` action.
- [ ] Backend lifecycle: detect/reuse local FastAPI or launch it.
- [x] LaunchAgent/install helper for autostart (`usagemon autostart` generates plists only; activation stays manual).
- [ ] Hermes plugin packaging: `hermes plugins enable api-usage-monitor` fails with "not installed or bundled" — the manifest references a `dist/index.js` desktop bundle that this repo does not build. The standalone app does not depend on it; making the Hermes plugin real is separate packaging work.

Why `rumps` first: fastest path, enough for local personal app, easy to replace with Swift/AppKit later.

## Phase 3 — provider intelligence

- [ ] Read-only Hermes profile discovery.
- [x] Read-only `state.db` usage adapter for configured Hermes DB paths/profiles.
- [ ] Merge “spent/used” from Hermes sessions with “remaining” from provider APIs.
- [ ] Generic OpenAI-compatible credits parser improvements.
- [x] External price-table cost-estimation library.
- [ ] Surface price-table estimates in snapshots/dashboard beyond state.db details.
- [ ] Provider-specific thresholds.
- [ ] Depletion projection per usage window.

## Phase 4 — alerts

- [x] macOS notifications for warning/error/quota states.
- [x] Dedup/snooze alerts to avoid spam.
- [ ] Threshold config in `providers.yaml`.
- [ ] Optional Companion inbox delivery later, if useful.

## Phase 5 — hardening

- [ ] Unit tests for adapters with recorded fake payloads.
- [ ] Contract tests for FastAPI endpoints.
- [ ] Installer smoke test with isolated `HERMES_HOME`.
- [ ] Better dependency isolation for standalone install.
- [ ] CI/local gate script.
- [x] Packaging docs for macOS autostart.

## Kimi CLI development workflow

Use Kimi as a secondary coding agent, not blind authority.

Suggested loop:

1. Hermes prepares a narrow task prompt and current repo context.
2. Run Kimi CLI in plan or auto mode on that slice.
3. Hermes reviews Kimi's diff/output.
4. Hermes runs tests/gates.
5. Hermes patches/fixes integration issues.
6. Repeat until the phase is done.

Kimi command shape:

```bash
kimi -p "Inspect this repo and propose the next implementation slice..."
kimi -y
# then paste: "Implement the macOS menu bar app slice..."
```

Do not use Kimi `/usage` in non-interactive `-p` mode; that sends `/usage` to the model instead of executing the slash command.

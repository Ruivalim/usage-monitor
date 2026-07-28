# Kimi CLI workflow

Kimi CLI is used as a secondary coding/planning agent for this repo. Hermes remains the integrator/reviewer: Kimi may propose or implement a slice, but Hermes reviews diffs, runs gates, and fixes integration issues before reporting work as done.

## Availability

Verified command:

```bash
kimi --help
```

Current CLI supports:

```bash
kimi -p "prompt"                  # non-interactive prompt mode
kimi -y                           # interactive yolo mode
kimi --auto                       # interactive fully-autonomous mode
kimi -c                           # continue previous session for this working directory
kimi --session <id>                # resume a known session
kimi export <sessionId>            # export a session
```

Important: this CLI does **not** allow `--plan`, `-y/--yolo`, or `--auto` together with `-p`; use `kimi -p` for non-interactive one-shot prompts, or open interactive yolo/auto mode separately.

## Read-only planning prompt

```bash
kimi -p "Do not modify files. Inspect this repo and return a concise implementation plan for the next slice..."
```

Use this before broad changes, architecture choices, or unfamiliar provider integrations.

## Implementation prompt shape

Use narrow slices. Example:

Launch interactive yolo/auto first:

```bash
kimi -y
# or
kimi --auto
```

Then paste a narrow implementation prompt:

```text
Implement only Slice N: <short title>.
Constraints:
- Do not touch unrelated files.
- Add tests for config loading, status coercion, snapshot persistence, and FastAPI endpoints.
- Do not use real credentials or network calls.
- Use temp dirs/env vars for USAGE_MONITOR_* state.
- Run pytest and report exact commands/results.
```

If interactive yolo/auto stalls, fall back to non-interactive `kimi -p "..."`; this worked for the Slice 1 test harness in this repo and produced file edits/tests.

After Kimi returns:

```bash
git diff --stat
git diff --check
python3.11 -m compileall usage_monitor_app plugin scripts
python3.11 -m pytest -q
```

Hermes then reviews the diff before moving to the next slice.

## Recommended remaining slices

1. **Tests first** — done in the current unreleased branch
   - pytest harness
   - config loader tests
   - registry dispatch tests
   - snapshot JSONL round-trip
   - FastAPI endpoint tests
   - adapter tests with monkeypatched HTTP payloads

2. **Price table logic**
   - pure `costs.py`
   - model pricing schema in `examples/prices.yaml`
   - no cross-currency summing

3. **Hermes `state.db` read-only adapter**
   - inspect schema first
   - connect with SQLite URI `mode=ro`
   - degrade to `unknown` on schema drift

4. **macOS alerts**
   - alert dedup/snooze state
   - `osascript display notification` fallback
   - wire into scheduler/CLI, not core collection

5. **Menu bar app**
   - likely `rumps` first
   - status icon/title
   - provider submenu
   - refresh/open dashboard actions
   - backend probe/spawn lifecycle

6. **Packaging/autostart**
   - LaunchAgent after app behavior is stable
   - isolated venv
   - install/uninstall reversal tested

## Rules

- Kimi should not commit/push.
- Kimi should not read or print secrets.
- Kimi should not run real provider checks unless explicitly requested.
- Prefer fake payloads and temp `USAGE_MONITOR_HOME` for tests.
- Hermes runs the final gates and owns the final report.

## Kimi `/usage` caveat

Do not use:

```bash
kimi -p "/usage"
```

That sends `/usage` to the model instead of executing the interactive slash command. Usage/quota introspection for Kimi still needs the real HTTP endpoint discovery or an interactive session.

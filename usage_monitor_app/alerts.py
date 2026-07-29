# SPDX-License-Identifier: MIT
"""Local alert dedup/snooze state and macOS notification plumbing.

Pure-ish module: no network, no credentials, no Hermes imports. The only
side effects are a small JSON state file (``alert_state.json``) and, when
enabled, local ``osascript display notification`` calls. Both are
injectable: pass explicit ``state_file``/``now`` values and a fake command
``runner`` to test without touching real state or notifying.

State schema (version 1)::

    {"version": 1, "alerts": {"<level>|<provider>|<message>": {
        "level": ..., "provider": ..., "message": ...,
        "first_notified_at": <epoch>, "last_notified_at": <epoch>}}}

An alert is "new" when its key is absent from state or its
``last_notified_at`` is older than the snooze window. Notification failures
never raise and never block state recording — dedup state is saved first so
a broken notifier cannot cause alert spam.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .core import APP_HOME

ALERT_STATE_FILE = Path(
    os.environ.get("USAGE_MONITOR_ALERT_STATE_FILE") or APP_HOME / "alert_state.json"
).expanduser()

DEFAULT_SNOOZE_SECONDS = 3600.0

STATE_VERSION = 1


def default_snooze_seconds() -> float:
    """Snooze window from ``USAGE_MONITOR_ALERT_SNOOZE_SECONDS`` (default 3600)."""
    try:
        value = float(os.environ.get("USAGE_MONITOR_ALERT_SNOOZE_SECONDS") or "")
    except (TypeError, ValueError):
        return DEFAULT_SNOOZE_SECONDS
    return value if value >= 0 else DEFAULT_SNOOZE_SECONDS


def notifications_enabled() -> bool:
    """Whether the scheduler/CLI should actually call osascript (default on)."""
    raw = os.environ.get("USAGE_MONITOR_ALERT_NOTIFY", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _coerce_alert(value: Any) -> Optional[dict[str, str]]:
    if not isinstance(value, dict):
        return None
    if not any(value.get(k) for k in ("level", "provider", "message", "status")):
        return None
    provider = str(value.get("provider") or "unknown")
    level = str(value.get("level") or "warning")
    message = str(value.get("message") or value.get("status") or level)
    return {"level": level, "provider": provider, "message": message}


def snapshot_alerts(snapshot: Any) -> list[dict[str, str]]:
    """Extract normalized alert dicts from a MonitorSnapshot or plain dict."""
    raw = snapshot.get("alerts") if isinstance(snapshot, dict) else getattr(snapshot, "alerts", None)
    out: list[dict[str, str]] = []
    for item in raw or []:
        alert = _coerce_alert(item)
        if alert is not None:
            out.append(alert)
    return out


def alert_key(alert: dict[str, Any]) -> str:
    """Dedup key: level + provider + message."""
    return "|".join(
        [
            str(alert.get("level") or ""),
            str(alert.get("provider") or ""),
            str(alert.get("message") or ""),
        ]
    )


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "alerts": {}}


def load_state(path: Path = ALERT_STATE_FILE) -> dict[str, Any]:
    """Load dedup state; missing/corrupt files yield an empty state."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), dict):
        return _empty_state()
    return data


def save_state(state: dict[str, Any], path: Path = ALERT_STATE_FILE) -> None:
    """Persist dedup state atomically (tmp file + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def compute_new_alerts(
    alerts: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    now: Optional[float] = None,
    snooze_seconds: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Return alerts not seen before or whose snooze window has elapsed.

    Pure: no I/O, no clock reads beyond the optional ``now`` default.
    """
    now = time.time() if now is None else float(now)
    snooze = default_snooze_seconds() if snooze_seconds is None else float(snooze_seconds)
    known = state.get("alerts") if isinstance(state, dict) else None
    known = known if isinstance(known, dict) else {}
    new: list[dict[str, Any]] = []
    for alert in alerts:
        entry = known.get(alert_key(alert))
        last = entry.get("last_notified_at") if isinstance(entry, dict) else None
        try:
            last_f = float(last) if last is not None else None
        except (TypeError, ValueError):
            last_f = None
        if last_f is None or last_f + snooze <= now:
            new.append(alert)
    return new


def record_notifications(
    state: dict[str, Any],
    alerts: list[dict[str, Any]],
    *,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Mark alerts as notified at ``now``; returns the updated state."""
    now = time.time() if now is None else float(now)
    if not isinstance(state.get("alerts"), dict):
        state["alerts"] = {}
    state["version"] = STATE_VERSION
    for alert in alerts:
        key = alert_key(alert)
        entry = state["alerts"].get(key)
        first = entry.get("first_notified_at") if isinstance(entry, dict) else None
        state["alerts"][key] = {
            "level": str(alert.get("level") or "warning"),
            "provider": str(alert.get("provider") or "unknown"),
            "message": str(alert.get("message") or ""),
            "first_notified_at": first if isinstance(first, (int, float)) else now,
            "last_notified_at": now,
        }
    return state


def notification_text(alert: dict[str, Any]) -> tuple[str, str]:
    """(title, body) for a macOS notification."""
    level = str(alert.get("level") or "warning").upper()
    provider = str(alert.get("provider") or "unknown")
    message = str(alert.get("message") or "")[:200]
    return f"API Usage Monitor — {level}", f"{provider}: {message}"


def _applescript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify_macos(
    alerts: list[dict[str, Any]],
    *,
    runner: Optional[Callable[..., Any]] = None,
) -> int:
    """Send one ``osascript display notification`` per alert; returns sent count.

    ``runner`` defaults to ``subprocess.run`` and is injectable for tests.
    Runner exceptions and non-zero exit codes are tolerated and simply not
    counted as sent.
    """
    run = runner if runner is not None else subprocess.run
    sent = 0
    for alert in alerts:
        title, body = notification_text(alert)
        script = f'display notification "{_applescript_escape(body)}" with title "{_applescript_escape(title)}"'
        try:
            result = run(["osascript", "-e", script])
        except Exception:
            continue
        if getattr(result, "returncode", 0) == 0:
            sent += 1
    return sent


def process_snapshot(
    snapshot: Any,
    *,
    state_file: Optional[Path] = None,
    snooze_seconds: Optional[float] = None,
    now: Optional[float] = None,
    notify: Optional[bool] = None,
    runner: Optional[Callable[..., Any]] = None,
) -> list[dict[str, str]]:
    """Compute new alerts from a snapshot, record them, optionally notify.

    Order: load state -> compute new alerts -> record + save state -> notify
    (best-effort). Returns the list of new alerts. ``notify=None`` follows
    ``notifications_enabled()`` (``USAGE_MONITOR_ALERT_NOTIFY``).
    """
    state_path = Path(state_file) if state_file is not None else ALERT_STATE_FILE
    state = load_state(state_path)
    new = compute_new_alerts(snapshot_alerts(snapshot), state, now=now, snooze_seconds=snooze_seconds)
    if not new:
        return []
    save_state(record_notifications(state, new, now=now), state_path)
    if notify if notify is not None else notifications_enabled():
        notify_macos(new, runner=runner)
    return new

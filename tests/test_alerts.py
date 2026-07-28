"""Alert dedup/snooze state and macOS notification plumbing.

All tests use fake snapshots and a fake command runner: no real state file
outside tmp_path, no real osascript calls, no real notifications.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from usage_monitor_app import alerts, core


class FakeRunner:
    """Injectable stand-in for subprocess.run."""

    def __init__(self, returncode: int = 0, raises: bool = False):
        self.returncode = returncode
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.raises:
            raise RuntimeError("boom")
        return SimpleNamespace(returncode=self.returncode)


def _snapshot(alert_list, checked_at="2026-07-26T00:00:00+00:00"):
    return core.MonitorSnapshot(
        checked_at=checked_at,
        overall="warning" if alert_list else "ok",
        providers=[],
        alerts=alert_list,
    )


def _alert(level="warning", provider="demo", message="nearly exhausted"):
    return {"level": level, "provider": provider, "message": message}


# --- pure computation -----------------------------------------------------


def test_compute_new_alerts_empty_state():
    new = alerts.compute_new_alerts([_alert(), _alert(provider="b")], alerts._empty_state(), now=1000.0)
    assert len(new) == 2


def test_compute_new_alerts_dedups_within_snooze():
    state = alerts.record_notifications(alerts._empty_state(), [_alert()], now=1000.0)
    assert alerts.compute_new_alerts([_alert()], state, now=1000.0 + 100, snooze_seconds=3600) == []


def test_compute_new_alerts_resends_after_snooze():
    state = alerts.record_notifications(alerts._empty_state(), [_alert()], now=1000.0)
    new = alerts.compute_new_alerts([_alert()], state, now=1000.0 + 3600, snooze_seconds=3600)
    assert new == [_alert()]


def test_snooze_seconds_configurable():
    state = alerts.record_notifications(alerts._empty_state(), [_alert()], now=1000.0)
    # snooze=0 means every occurrence is new again
    assert alerts.compute_new_alerts([_alert()], state, now=1000.0, snooze_seconds=0) == [_alert()]
    # a long custom snooze suppresses re-notification
    assert alerts.compute_new_alerts([_alert()], state, now=1000.0 + 5000, snooze_seconds=99999) == []


def test_alert_key_distinguishes_level_provider_message():
    base = alerts.alert_key(_alert())
    assert base != alerts.alert_key(_alert(level="error"))
    assert base != alerts.alert_key(_alert(provider="other"))
    assert base != alerts.alert_key(_alert(message="different"))
    assert base == alerts.alert_key(_alert())


def test_snapshot_alerts_accepts_dicts_and_snapshots():
    snap = _snapshot([_alert(), {"unexpected": "shape"}, "junk"])
    assert alerts.snapshot_alerts(snap) == [_alert()]
    assert alerts.snapshot_alerts({"alerts": [_alert()]}) == [_alert()]
    assert alerts.snapshot_alerts({"alerts": []}) == []


def test_default_snooze_seconds_from_env(monkeypatch):
    monkeypatch.delenv("USAGE_MONITOR_ALERT_SNOOZE_SECONDS", raising=False)
    assert alerts.default_snooze_seconds() == alerts.DEFAULT_SNOOZE_SECONDS
    monkeypatch.setenv("USAGE_MONITOR_ALERT_SNOOZE_SECONDS", "120")
    assert alerts.default_snooze_seconds() == 120.0
    monkeypatch.setenv("USAGE_MONITOR_ALERT_SNOOZE_SECONDS", "not-a-number")
    assert alerts.default_snooze_seconds() == alerts.DEFAULT_SNOOZE_SECONDS


# --- state persistence ----------------------------------------------------


def test_state_roundtrip(tmp_path):
    path = tmp_path / "alert_state.json"
    state = alerts.record_notifications(alerts._empty_state(), [_alert()], now=1234.5)
    alerts.save_state(state, path)
    loaded = alerts.load_state(path)
    entry = loaded["alerts"][alerts.alert_key(_alert())]
    assert entry["last_notified_at"] == 1234.5
    assert entry["first_notified_at"] == 1234.5
    assert entry["provider"] == "demo"


def test_record_notifications_keeps_first_notified_at():
    state = alerts.record_notifications(alerts._empty_state(), [_alert()], now=100.0)
    state = alerts.record_notifications(state, [_alert()], now=200.0)
    entry = state["alerts"][alerts.alert_key(_alert())]
    assert entry["first_notified_at"] == 100.0
    assert entry["last_notified_at"] == 200.0


def test_load_state_tolerates_missing_and_corrupt(tmp_path):
    assert alerts.load_state(tmp_path / "nope.json") == alerts._empty_state()
    bad = tmp_path / "bad.json"
    bad.write_text("not json{", encoding="utf-8")
    assert alerts.load_state(bad) == alerts._empty_state()
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"alerts": [1, 2]}', encoding="utf-8")
    assert alerts.load_state(wrong) == alerts._empty_state()


# --- macOS notifications (fake runner) ------------------------------------


def test_notify_macos_builds_osascript_command():
    runner = FakeRunner()
    sent = alerts.notify_macos([_alert(provider="deepseek", message='quota "gone"')], runner=runner)
    assert sent == 1
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[:2] == ["osascript", "-e"]
    script = cmd[2]
    assert script.startswith('display notification "')
    assert 'with title "API Usage Monitor — WARNING"' in script
    assert 'deepseek: quota \\"gone\\"' in script  # quotes escaped for AppleScript


def test_notify_macos_tolerates_failures():
    assert alerts.notify_macos([_alert()], runner=FakeRunner(raises=True)) == 0
    assert alerts.notify_macos([_alert()], runner=FakeRunner(returncode=1)) == 0


def test_notification_text_truncates_long_messages():
    title, body = alerts.notification_text(_alert(message="x" * 500))
    assert title == "API Usage Monitor — WARNING"
    assert len(body) <= len("demo: ") + 200


# --- process_snapshot end-to-end ------------------------------------------


def test_process_snapshot_notifies_once_then_dedups(tmp_path):
    path = tmp_path / "alert_state.json"
    runner = FakeRunner()
    snap = _snapshot([_alert()])

    new = alerts.process_snapshot(snap, state_file=path, runner=runner, now=1000.0)
    assert new == [_alert()]
    assert len(runner.calls) == 1

    # same alert inside the snooze window: no state change, no notification
    assert alerts.process_snapshot(snap, state_file=path, runner=runner, now=1001.0) == []
    assert len(runner.calls) == 1

    # after the snooze window: notified again
    new = alerts.process_snapshot(snap, state_file=path, runner=runner, now=1000.0 + 3600)
    assert new == [_alert()]
    assert len(runner.calls) == 2


def test_process_snapshot_records_state_even_when_notify_disabled(tmp_path):
    path = tmp_path / "alert_state.json"
    runner = FakeRunner()
    new = alerts.process_snapshot(_snapshot([_alert()]), state_file=path, runner=runner, notify=False, now=1000.0)
    assert new == [_alert()]
    assert runner.calls == []
    assert path.exists()  # dedup state still persisted


def test_process_snapshot_notify_none_follows_env(tmp_path, monkeypatch):
    path = tmp_path / "alert_state.json"
    runner = FakeRunner()
    monkeypatch.setenv("USAGE_MONITOR_ALERT_NOTIFY", "0")
    alerts.process_snapshot(_snapshot([_alert()]), state_file=path, runner=runner, now=1000.0)
    assert runner.calls == []
    monkeypatch.setenv("USAGE_MONITOR_ALERT_NOTIFY", "1")
    alerts.process_snapshot(_snapshot([_alert(provider="b")]), state_file=path, runner=runner, now=1001.0)
    assert len(runner.calls) == 1


def test_process_snapshot_no_alerts_writes_nothing(tmp_path):
    path = tmp_path / "alert_state.json"
    assert alerts.process_snapshot(_snapshot([]), state_file=path, runner=FakeRunner()) == []
    assert not path.exists()


def test_process_snapshot_custom_snooze(tmp_path):
    path = tmp_path / "alert_state.json"
    runner = FakeRunner()
    snap = _snapshot([_alert()])
    alerts.process_snapshot(snap, state_file=path, runner=runner, snooze_seconds=60, now=1000.0)
    assert alerts.process_snapshot(snap, state_file=path, runner=runner, snooze_seconds=60, now=1059.0) == []
    assert alerts.process_snapshot(snap, state_file=path, runner=runner, snooze_seconds=60, now=1060.0) == [_alert()]


def test_process_snapshot_runner_failure_still_records(tmp_path):
    path = tmp_path / "alert_state.json"
    runner = FakeRunner(raises=True)
    new = alerts.process_snapshot(_snapshot([_alert()]), state_file=path, runner=runner, now=1000.0)
    assert new == [_alert()]
    # state saved before notify, so a broken notifier cannot cause spam
    assert alerts.process_snapshot(_snapshot([_alert()]), state_file=path, runner=runner, now=1001.0) == []


# --- scheduler wiring ------------------------------------------------------


def test_scheduler_processes_alerts_after_persist(monkeypatch, tmp_path):
    """web._scheduler must run alert processing after collect_status(persist=True)."""
    from usage_monitor_app import web

    calls = []
    snap = _snapshot([_alert()])

    def fake_collect(*, persist):
        calls.append(("collect", persist))
        return snap

    def fake_process(snapshot, **kwargs):
        calls.append(("process", snapshot is snap))
        return []

    monkeypatch.setattr(web, "collect_status", fake_collect)
    monkeypatch.setattr(web._alerts, "process_snapshot", fake_process)

    import threading

    stop = threading.Event()
    thread = threading.Thread(target=web._scheduler, args=(stop, 0.01), daemon=True)
    thread.start()
    import time

    deadline = time.time() + 5
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2)

    assert ("collect", True) in calls
    assert ("process", True) in calls
    # ordering: collect always precedes process
    first = calls.index(("collect", True))
    assert calls[first + 1] == ("process", True)

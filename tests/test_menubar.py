"""Headless tests for the optional rumps menu bar app.

Everything GUI-facing is faked: a FakeRumps module stands in for `rumps`,
webbrowser is replaced by a recorder, and snapshot loading / refresh requests
are injected callables. No real GUI, no network, no launchctl.
"""
from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from usage_monitor_app import menubar
from usage_monitor_app.core import Money, MonitorSnapshot, ProviderStatus, UsageWindow


# --- fakes -----------------------------------------------------------------

class FakeMenuItem:
    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback
        self.state = 0

    def set_callback(self, callback):
        self.callback = callback


class FakeMenu(dict):
    def add(self, item):
        self[item.title] = item


class FakeApp:
    def __init__(self, name, title=None, quit_button=None):
        self.name = name
        self.title = title
        self.quit_button = quit_button
        self.menu = FakeMenu()
        self.ran = False

    def run(self):
        self.ran = True


class FakeTimer:
    def __init__(self, callback, interval):
        self.callback = callback
        self.interval = interval
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def fake_rumps():
    return SimpleNamespace(
        App=FakeApp,
        MenuItem=FakeMenuItem,
        Timer=FakeTimer,
        separator=FakeMenuItem("---separator---"),
    )


class FakeWebbrowser:
    def __init__(self):
        self.opened = []

    def open(self, url):
        self.opened.append(url)


class FakeStartup:
    def __init__(self, installed=False):
        self._installed = installed
        self.calls = []

    def installed(self):
        return self._installed

    def install(self):
        self.calls.append("install")
        self._installed = True

    def uninstall(self):
        self.calls.append("uninstall")
        self._installed = False


def make_snapshot(overall="warning", providers=None):
    providers = providers if providers is not None else [
        ProviderStatus(
            id="openrouter",
            label="OpenRouter",
            status="ok",
            source="test",
            balance=Money(12.34, "USD"),
            windows=[UsageWindow(label="API key quota", used_percent=42.0, remaining_percent=58.0)],
        ),
        ProviderStatus(
            id="deepseek",
            label="DeepSeek",
            status="warning",
            source="test",
            message="low balance",
        ),
    ]
    return MonitorSnapshot(checked_at="2026-07-26T00:00:00+00:00", overall=overall, providers=providers, alerts=[], meta={})


def make_snapshot_dict(overall="warning", providers=None):
    providers = providers if providers is not None else [
        {
            "id": "deepseek",
            "label": "DeepSeek",
            "status": "ok",
            "balance": {"amount": 12.34, "currency": "USD"},
            "windows": [{"label": "API key quota", "remaining_percent": 58.0}],
        },
        {"id": "auth:zai", "label": "Auth: zai", "status": "ok"},
        {"id": "nous", "label": "Nous Portal", "status": "quota_exhausted", "relevant": False},
    ]
    return {"checked_at": "2026-07-26T00:00:00+00:00", "overall": overall, "providers": providers, "alerts": []}


def build_app(load_snapshot=None, request_refresh=None, startup=None, **kwargs):
    kwargs.setdefault("rumps_module", fake_rumps())
    kwargs.setdefault("webbrowser_module", FakeWebbrowser())
    kwargs.setdefault("refresh_interval", 0)
    kwargs.setdefault("poll_interval", 0)  # no timer by default in tests
    return menubar.UsageMonitorMenuBarApp(
        load_snapshot=load_snapshot or (lambda: make_snapshot_dict()),
        request_refresh=request_refresh or (lambda: None),
        startup_manager=startup if startup is not None else FakeStartup(),
        **kwargs,
    )


def menu_titles(app):
    return list(app.app.menu.keys())


# --- pure helpers ------------------------------------------------------------

def test_overall_title_icons():
    assert menubar.overall_title(make_snapshot(overall="ok")) == "🟢 APIs"
    assert menubar.overall_title(make_snapshot(overall="error")) == "🔴 APIs"
    assert menubar.overall_title(make_snapshot(overall="bogus")) == "⚪ APIs"
    assert menubar.overall_title_from_dict({"overall": "ok"}) == "🟢 APIs"


def test_provider_line_formatting():
    p = ProviderStatus(
        id="x",
        label="Provider X",
        status="rate_limited",
        source="test",
        balance=Money(3.5, "USD"),
        windows=[UsageWindow(label="Weekly", used_percent=80.0, remaining_percent=20.0)],
        message="slow down",
    )
    line = menubar.provider_line(p)
    assert line == "🟠 Provider X: rate_limited 3.50 USD | Weekly: 20% left — slow down"


def test_provider_line_muted_marker():
    p = ProviderStatus(id="n", label="Nous", status="quota_exhausted", relevant=False)
    assert menubar.provider_line(p).endswith("(muted)")


def test_provider_line_from_dict():
    p = {
        "label": "DeepSeek",
        "status": "ok",
        "balance": {"amount": 7.06, "currency": "USD"},
        "windows": [{"label": "Weekly", "remaining_percent": 42.0}],
        "message": "note",
        "relevant": False,
    }
    line = menubar.provider_line_from_dict(p)
    assert line == "🟢 DeepSeek: ok 7.06 USD | Weekly: 42% left — note (muted)"


# --- app behavior -------------------------------------------------------------

def test_load_populates_title_and_menu():
    app = build_app()
    assert app.app.title == "🟡 APIs"
    titles = menu_titles(app)
    assert "Refresh Now" in titles
    assert "Open Dashboard" in titles
    assert "Show auth" in titles
    assert "Open at startup" in titles
    assert "Quit Usage Monitor" in titles
    assert "🟢 DeepSeek: ok 12.34 USD | API key quota: 58% left" in titles


def test_auth_providers_hidden_until_toggled():
    app = build_app()
    assert not any(t.startswith("🟢 Auth: zai") for t in menu_titles(app))
    app.auth_item.callback(app.auth_item)  # simulate clicking "Mostrar auth"
    assert any(t.startswith("🟢 Auth: zai") for t in menu_titles(app))
    assert app.auth_item.state == 1
    app.auth_item.callback(app.auth_item)
    assert not any(t.startswith("🟢 Auth: zai") for t in menu_titles(app))
    assert app.auth_item.state == 0


def test_startup_toggle_installs_and_uninstalls():
    startup = FakeStartup(installed=False)
    app = build_app(startup=startup)
    assert app.startup_item.state == 0
    app.startup_item.callback(app.startup_item)
    assert startup.calls == ["install"]
    assert app.startup_item.state == 1
    app.startup_item.callback(app.startup_item)
    assert startup.calls == ["install", "uninstall"]
    assert app.startup_item.state == 0


def test_refresh_now_runs_in_worker_thread_and_reloads():
    done = threading.Event()
    state = {"snap": make_snapshot_dict(overall="warning")}

    def request_refresh():
        state["snap"] = make_snapshot_dict(overall="ok")
        done.set()

    app = build_app(load_snapshot=lambda: state["snap"], request_refresh=request_refresh)
    assert app.app.title == "🟡 APIs"
    app.refresh_item.callback(app.refresh_item)  # simulate clicking "Refresh Now"
    assert done.wait(timeout=5)
    for _ in range(100):
        app._tick(None)
        if app.app.title == "🟢 APIs":
            break
    assert app.app.title == "🟢 APIs"


def test_tick_reloads_when_backend_snapshot_file_changes():
    state = {
        "snap": make_snapshot_dict(overall="warning"),
        "token": (1, 100),
    }

    app = build_app(
        load_snapshot=lambda: state["snap"],
        snapshot_token=lambda: state["token"],
    )
    assert app.app.title == "🟡 APIs"

    state["snap"] = make_snapshot_dict(overall="ok")
    state["token"] = (2, 200)
    app._tick(None)

    assert app.app.title == "🟢 APIs"


def test_refresh_failure_shows_error_without_raising():
    done = threading.Event()

    def boom():
        done.set()
        raise RuntimeError("collect exploded")

    app = build_app(request_refresh=boom)
    app.refresh_now()
    assert done.wait(timeout=5)
    for _ in range(100):
        app._tick(None)
        if any("collect exploded" in t for t in menu_titles(app)):
            break
    titles = menu_titles(app)
    assert any("refresh failed" in t and "collect exploded" in t for t in titles)
    assert "Refresh Now" in titles and "Open Dashboard" in titles


def test_missing_snapshot_shows_collecting_and_triggers_refresh():
    done = threading.Event()

    def request_refresh():
        done.set()

    app = build_app(load_snapshot=lambda: None, request_refresh=request_refresh)
    assert "collecting…" in menu_titles(app)
    assert done.wait(timeout=5)  # missing snapshot triggers a background refresh


def test_no_providers_line():
    app = build_app(load_snapshot=lambda: make_snapshot_dict(overall="ok", providers=[]))
    assert "No providers configured" in menu_titles(app)


def test_open_dashboard_action():
    app = build_app(dashboard_url="http://127.0.0.1:1234")
    app.dashboard_item.callback(app.dashboard_item)
    assert app._webbrowser.opened == ["http://127.0.0.1:1234"]


def test_run_starts_timer_and_app():
    app = build_app(poll_interval=5)
    assert app._timer is not None and app._timer.interval == 5
    app.run()
    assert app._timer.started is True
    assert app.app.ran is True


def test_poll_interval_zero_disables_timer():
    app = build_app(poll_interval=0)
    assert app._timer is None
    app.run()
    assert app.app.ran is True


def test_tick_triggers_periodic_refresh():
    done = threading.Event()
    calls = {"n": 0}

    def request_refresh():
        calls["n"] += 1
        done.set()

    app = build_app(request_refresh=request_refresh, refresh_interval=10)
    app._last_request = 0.0  # make the interval due
    app._tick(None)
    assert done.wait(timeout=5)
    assert calls["n"] == 1
    app._tick(None)  # still within the interval -> no second refresh
    assert calls["n"] == 1


# --- rumps absence / CLI entrypoints -------------------------------------------

def test_load_rumps_missing_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "rumps", None)
    with pytest.raises(RuntimeError, match="rumps"):
        menubar._load_rumps()


def test_main_returns_2_without_rumps(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "rumps", None)
    rc = menubar.main(["--interval", "0"])
    assert rc == 2
    assert "rumps" in capsys.readouterr().err


def test_main_with_fake_rumps(monkeypatch):
    fake = fake_rumps()
    monkeypatch.setattr(menubar, "_load_rumps", lambda: fake)
    monkeypatch.setattr(menubar, "collect_status", lambda persist=True: make_snapshot(overall="ok"))
    rc = menubar.main(["--interval", "0", "--poll-interval", "0", "--dashboard-url", "http://x:1"])
    assert rc == 0


def test_usagectl_menubar_subcommand_missing_rumps(monkeypatch, capsys):
    from scripts import usagectl

    monkeypatch.setitem(sys.modules, "rumps", None)
    monkeypatch.setattr(sys, "argv", ["usagectl", "menubar", "--interval", "0"])
    rc = usagectl.main()
    assert rc == 2
    assert "rumps" in capsys.readouterr().err

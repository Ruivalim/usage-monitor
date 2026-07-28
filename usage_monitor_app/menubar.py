"""Optional macOS menu bar app for the standalone usage monitor.

The tray is a thin, fast view: it reads the latest persisted snapshot (a
static JSONL entry written by the backend scheduler or by a refresh request)
and never blocks the GUI thread on network calls. "Refresh Now" runs in a
worker thread and asks the backend to re-collect (falling back to a local
collect when the backend is offline); when it finishes, the next UI tick
re-reads the snapshot file.

Requires the optional ``rumps`` dependency (install extra: ``[menubar]``).
This module stays importable without rumps; the import only happens inside
``_load_rumps()`` when the app is actually started.

All GUI-facing dependencies (the rumps module, snapshot loader, refresh
requester, startup manager, webbrowser) are injectable so tests can run
headless with fakes.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Any, Callable, Optional

from .core import MonitorSnapshot, ProviderStatus, SNAPSHOT_FILE, collect_status, latest_snapshot
from .config import DEFAULT_LANGUAGE, load_app_config

DEFAULT_DASHBOARD_URL = "http://127.0.0.1:9097"
DEFAULT_REFRESH_INTERVAL = 300
DEFAULT_POLL_INTERVAL = 5

TITLE_ICONS = {"ok": "🟢", "warning": "🟡", "error": "🔴", "unknown": "⚪"}
PROVIDER_ICONS = {
    "ok": "🟢",
    "warning": "🟡",
    "rate_limited": "🟠",
    "quota_exhausted": "🔴",
    "error": "🔴",
    "unavailable": "⚫",
    "unknown": "⚪",
}

STRINGS = {
    "en": {
        "quit": "Quit Usage Monitor", "refresh": "Refresh Now", "dashboard": "Open Dashboard",
        "show_auth": "Show auth", "startup": "Open at startup", "no_providers": "No providers configured",
        "collecting": "collecting…", "refresh_failed": "refresh failed", "left": "left",
        "resets_in": "resets in", "now": "now", "muted": "muted",
    },
    "pt": {
        "quit": "Fechar Usage Monitor", "refresh": "Atualizar agora", "dashboard": "Abrir dashboard",
        "show_auth": "Mostrar auth", "startup": "Abrir no startup", "no_providers": "Nenhum provider configurado",
        "collecting": "coletando…", "refresh_failed": "falha ao atualizar", "left": "restante",
        "resets_in": "reset em", "now": "agora", "muted": "mudo",
    },
}


def _lang(value: str | None = None) -> str:
    value = (value or DEFAULT_LANGUAGE).lower()
    if value in ("pt-br", "pt_br"):
        return "pt"
    return value if value in STRINGS else "en"


def _t(language: str, key: str) -> str:
    return STRINGS.get(_lang(language), STRINGS["en"]).get(key, STRINGS["en"].get(key, key))


def _load_rumps() -> Any:
    try:
        import rumps  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The macOS menu bar app requires the optional 'rumps' package. "
            "Install it with: pip install 'usage-monitor[menubar]' "
            "(or: pip install rumps)"
        ) from exc
    return rumps


def overall_title(snap: MonitorSnapshot) -> str:
    return f"{TITLE_ICONS.get(snap.overall, '⚪')} APIs"


def overall_title_from_dict(snap: dict) -> str:
    return f"{TITLE_ICONS.get(str(snap.get('overall')), '⚪')} APIs"


def provider_line(p: ProviderStatus, *, language: str = "en") -> str:
    """Compact one-line provider summary, mirroring core.render_text."""
    icon = PROVIDER_ICONS.get(p.status, "⚪")
    line = f"{icon} {p.label}: {p.status}"
    if p.balance is not None:
        line += f" {p.balance.amount:.2f} {p.balance.currency}"
    if p.windows:
        parts = []
        for w in p.windows:
            if w.remaining_percent is not None:
                entry = f"{w.label}: {w.remaining_percent:.0f}% {_t(language, 'left')}"
                rl = w.reset_label()
                if rl:
                    entry += f" · {_t(language, 'resets_in')} {rl if rl != 'now' else _t(language, 'now')}"
                parts.append(entry)
        if parts:
            line += " | " + "; ".join(parts)
    if p.message:
        line += f" — {p.message}"
    if not p.relevant:
        line += f" ({_t(language, 'muted')})"
    return line


def _reset_label_from_dict(w: dict, *, language: str = "en") -> str | None:
    """Human-readable reset countdown from a serialized window dict."""
    days = w.get("days_until_reset")
    hours = w.get("hours_until_reset")
    if days is None:
        return None
    if days > 0:
        return f"{int(days)}d"
    if hours is not None and hours > 0:
        return f"{int(hours)}h"
    return _t(language, "now")


def provider_line_from_dict(p: dict, *, language: str = "en") -> str:
    """Same rendering as provider_line, but for a serialized snapshot dict."""
    icon = PROVIDER_ICONS.get(str(p.get("status")), "⚪")
    line = f"{icon} {p.get('label')}: {p.get('status')}"
    bal = p.get("balance")
    if isinstance(bal, dict) and bal.get("amount") is not None:
        line += f" {float(bal['amount']):.2f} {bal.get('currency') or 'USD'}"
    parts = []
    for w in (p.get("windows") or []):
        if isinstance(w, dict) and w.get("remaining_percent") is not None:
            entry = f"{w.get('label')}: {float(w['remaining_percent']):.0f}% {_t(language, 'left')}"
            rl = _reset_label_from_dict(w, language=language)
            if rl:
                entry += f" · {_t(language, 'resets_in')} {rl}"
            parts.append(entry)
    if parts:
        line += " | " + "; ".join(parts)
    if p.get("message"):
        line += f" — {p['message']}"
    if p.get("relevant") is False:
        line += f" ({_t(language, 'muted')})"
    return line


def load_latest_snapshot() -> Optional[dict]:
    """Static read of the newest persisted snapshot; never touches the network."""
    snaps = latest_snapshot(1)
    return snaps[0] if snaps else None


def snapshot_file_token() -> tuple[int, int] | None:
    """Cheap change detector for snapshots written by the backend process."""
    try:
        stat = SNAPSHOT_FILE.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def make_refresh_requester(dashboard_url: str, *, persist: bool = True) -> Callable[[], None]:
    """Blocking refresh: ask the backend to re-collect; fall back to local collect.

    Meant to run in a worker thread, never on the GUI thread.
    """
    url = dashboard_url.rstrip("/") + "/refresh"

    def request() -> None:
        try:
            req = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=180):
                pass
        except Exception:
            collect_status(persist=persist)

    return request


class LocalStartupManager:
    """Install/remove LaunchAgents from the tray (start-at-login only).

    Install writes both plists and bootstraps the *server* agent (the tray
    is already running — bootstrapping it again is pointless). Uninstall
    only removes the plist files: current processes keep running until
    quit/logout, so the tray never kills itself mid-callback.
    """

    def __init__(self, config: Any = None) -> None:
        from .autostart import AutostartConfig

        self._config = config or AutostartConfig(python_executable=sys.executable)

    def installed(self) -> bool:
        try:
            from .autostart import agent_status

            return bool(agent_status(self._config)["installed"])
        except Exception:
            return False

    def install(self) -> None:
        from .autostart import _default_launchagents_dir, bootstrap_agent, write_plists

        directory = _default_launchagents_dir()
        directory.mkdir(parents=True, exist_ok=True)
        write_plists(self._config, directory)
        bootstrap_agent(self._config, self._config.server_label)

    def uninstall(self) -> None:
        from .autostart import _default_launchagents_dir

        directory = _default_launchagents_dir()
        for label in (self._config.server_label, self._config.menubar_label):
            path = directory / f"{label}.plist"
            if path.exists():
                path.unlink()


class UsageMonitorMenuBarApp:
    """rumps-based menu bar app. Everything GUI-related is injected."""

    def __init__(
        self,
        *,
        rumps_module: Any,
        load_snapshot: Callable[[], Optional[dict]] = load_latest_snapshot,
        snapshot_token: Callable[[], Any] = snapshot_file_token,
        request_refresh: Optional[Callable[[], None]] = None,
        startup_manager: Any = None,
        dashboard_url: str = DEFAULT_DASHBOARD_URL,
        refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        language: str = "en",
        webbrowser_module: Any = None,
    ) -> None:
        self._rumps = rumps_module
        self._load_snapshot = load_snapshot
        self._snapshot_token = snapshot_token
        self._request_refresh = request_refresh or make_refresh_requester(dashboard_url)
        self._startup = startup_manager
        self._dashboard_url = dashboard_url
        self._refresh_interval = max(0, refresh_interval)
        self._language = _lang(language)
        self._webbrowser = webbrowser_module if webbrowser_module is not None else webbrowser
        self._show_auth = False
        self._refreshing = False
        self._refresh_error: Optional[str] = None
        self._pending_reload = True
        self._last_snapshot_token: Any = object()
        self._last_request = time.monotonic()

        self.app = rumps_module.App("Usage Monitor", title="⚪ APIs", quit_button=None)
        self.refresh_item = rumps_module.MenuItem(_t(self._language, "refresh"), callback=self.refresh_now)
        self.dashboard_item = rumps_module.MenuItem(_t(self._language, "dashboard"), callback=self.open_dashboard)
        self.auth_item = rumps_module.MenuItem(_t(self._language, "show_auth"), callback=self.toggle_auth)
        self.startup_item = rumps_module.MenuItem(_t(self._language, "startup"), callback=self.toggle_startup)
        self.quit_item = rumps_module.MenuItem(_t(self._language, "quit"), callback=self.quit_app)
        self._timer = None
        if poll_interval and poll_interval > 0:
            self._timer = rumps_module.Timer(self._tick, poll_interval)
        self._tick(None)

    # --- periodic tick (fast, GUI thread) --------------------------------------

    def _tick(self, sender: Any = None) -> None:
        token = self._snapshot_token()
        if token != self._last_snapshot_token:
            self._last_snapshot_token = token
            self._pending_reload = True
        if (
            self._refresh_interval > 0
            and not self._refreshing
            and (time.monotonic() - self._last_request) >= self._refresh_interval
        ):
            self._start_refresh()
        if self._pending_reload:
            self._pending_reload = False
            self._reload_from_snapshot()

    # --- actions -----------------------------------------------------------------

    def refresh_now(self, sender: Any = None) -> None:
        self._start_refresh()

    def open_dashboard(self, sender: Any = None) -> None:
        self._webbrowser.open(self._dashboard_url)

    def toggle_auth(self, sender: Any = None) -> None:
        self._show_auth = not self._show_auth
        self._pending_reload = True
        self._tick(None)

    def toggle_startup(self, sender: Any = None) -> None:
        if self._startup is None:
            return
        try:
            if self._startup_installed():
                self._startup.uninstall()
            else:
                self._startup.install()
        except Exception as exc:
            self._refresh_error = f"startup: {exc}"
        self._pending_reload = True
        self._tick(None)

    def quit_app(self, sender: Any = None) -> None:
        quit_fn = getattr(self._rumps, "quit_application", None)
        if callable(quit_fn):
            quit_fn()
            return
        app_quit = getattr(self.app, "quit", None)
        if callable(app_quit):
            app_quit()

    # --- internals ----------------------------------------------------------------

    def _startup_installed(self) -> bool:
        try:
            return bool(self._startup and self._startup.installed())
        except Exception:
            return False

    def _start_refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self._refresh_error = None
        self._last_request = time.monotonic()
        self.app.title = "… APIs"

        def work() -> None:
            try:
                self._request_refresh()
            except Exception as exc:
                self._refresh_error = str(exc)[:200]
            finally:
                self._refreshing = False
                self._pending_reload = True

        threading.Thread(target=work, daemon=True).start()

    def _reload_from_snapshot(self) -> None:
        snap = self._load_snapshot()
        if snap is None:
            self.app.title = TITLE_ICONS["unknown"] + " APIs"
            lines = []
            if self._refresh_error:
                lines.append(f"⚠️ {_t(self._language, 'refresh_failed')}: {self._refresh_error}")
            lines.append(_t(self._language, "collecting"))
            self._rebuild_menu(lines)
            if not self._refreshing:
                self._start_refresh()
            return
        self.app.title = overall_title_from_dict(snap)
        providers = snap.get("providers") or []
        lines = [
            provider_line_from_dict(p, language=self._language)
            for p in providers
            if self._show_auth or not str(p.get("id", "")).startswith("auth:")
        ]
        if self._refresh_error:
            lines.insert(0, f"⚠️ {_t(self._language, 'refresh_failed')}: {self._refresh_error}")
        self._rebuild_menu(lines or [_t(self._language, "no_providers")])

    def _rebuild_menu(self, provider_lines: list[str]) -> None:
        self.auth_item.state = 1 if self._show_auth else 0
        self.startup_item.state = 1 if self._startup_installed() else 0
        menu = self.app.menu
        menu.clear()
        menu.add(self.refresh_item)
        menu.add(self.dashboard_item)
        menu.add(self.auth_item)
        menu.add(self.startup_item)
        menu.add(self._rumps.separator)
        for line in provider_lines:
            menu.add(self._rumps.MenuItem(line, callback=None))
        menu.add(self._rumps.separator)
        menu.add(self.quit_item)

    def run(self) -> None:
        if self._timer is not None:
            self._timer.start()
        self.app.run()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def main(argv: Optional[list[str]] = None) -> int:
    app_config = load_app_config()
    parser = argparse.ArgumentParser(description="macOS menu bar app for the usage monitor (requires the rumps extra)")
    parser.add_argument("--interval", type=int, default=app_config.intervals.menubar_seconds, help="background re-collect interval in seconds; 0 disables")
    parser.add_argument("--poll-interval", type=int, default=_env_int("USAGE_MONITOR_MENUBAR_POLL_INTERVAL", DEFAULT_POLL_INTERVAL), help="UI tick interval in seconds; 0 disables")
    parser.add_argument("--dashboard-url", default=os.environ.get("USAGE_MONITOR_DASHBOARD_URL") or DEFAULT_DASHBOARD_URL, help="URL opened by 'Open Dashboard' and used for refresh requests")
    parser.add_argument("--language", choices=["en", "pt"], default=app_config.dashboard.language, help="tray language (default: config.yaml dashboard.language)")
    parser.add_argument("--no-persist", action="store_true", help="do not append snapshots when collecting locally")
    args = parser.parse_args(argv)

    try:
        rumps = _load_rumps()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    app = UsageMonitorMenuBarApp(
        rumps_module=rumps,
        load_snapshot=load_latest_snapshot,
        snapshot_token=snapshot_file_token,
        request_refresh=make_refresh_requester(args.dashboard_url, persist=not args.no_persist),
        startup_manager=LocalStartupManager(),
        dashboard_url=args.dashboard_url,
        refresh_interval=args.interval,
        poll_interval=args.poll_interval,
        language=args.language,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

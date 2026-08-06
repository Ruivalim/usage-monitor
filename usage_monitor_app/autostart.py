# SPDX-License-Identifier: MIT
"""macOS LaunchAgent plist generation for the standalone usage monitor.

Generates launchd property lists for the standalone FastAPI backend
(``usagemon serve``) and optionally the rumps menu bar app
(``usagemon menubar``).

The generation functions are pure file generation: they never call
``launchctl``, never load/enable/start any agent, and never write anywhere
unless the caller explicitly passes an output directory. All paths (python
executable, working directory, usagectl script, log files) are explicit and
configurable.

The ``agent_status``/``install_agents``/``uninstall_agents`` helpers DO call
``launchctl`` — they exist for explicit user actions (Makefile targets,
dashboard buttons), never run implicitly.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_LABEL_PREFIX = "com.usage-monitor"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9097
DEFAULT_REFRESH_INTERVAL = 900
DEFAULT_MENUBAR_INTERVAL = 300


def _default_log_dir() -> Path:
    from .core import default_app_home

    state_home = os.environ.get("USAGE_MONITOR_HOME") or str(default_app_home())
    return Path(state_home).expanduser() / "logs"


def _default_usagectl(working_dir: Path) -> Path:
    return working_dir / "scripts" / "usagectl.py"


@dataclass
class AutostartConfig:
    """Explicit, fully configurable inputs for LaunchAgent generation."""

    python_executable: str = sys.executable
    working_dir: str = str(REPO_ROOT)
    usagectl_path: str | None = None  # default: <working_dir>/scripts/usagectl.py
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL  # backend scheduler seconds; 0 disables
    log_dir: str | None = None  # default: $USAGE_MONITOR_HOME/logs
    dashboard_url: str | None = None  # default: http://<host>:<port>
    menubar_interval: int = DEFAULT_MENUBAR_INTERVAL  # menubar auto-refresh seconds; 0 disables
    label_prefix: str = DEFAULT_LABEL_PREFIX

    @property
    def resolved_usagectl_path(self) -> Path:
        if self.usagectl_path:
            return Path(self.usagectl_path).expanduser()
        return _default_usagectl(Path(self.working_dir).expanduser())

    @property
    def resolved_log_dir(self) -> Path:
        if self.log_dir:
            return Path(self.log_dir).expanduser()
        return _default_log_dir()

    @property
    def resolved_dashboard_url(self) -> str:
        return self.dashboard_url or f"http://{self.host}:{self.port}"

    @property
    def server_label(self) -> str:
        return f"{self.label_prefix}.server"

    @property
    def menubar_label(self) -> str:
        return f"{self.label_prefix}.menubar"


def server_program_arguments(config: AutostartConfig) -> list[str]:
    return [
        config.python_executable,
        str(config.resolved_usagectl_path),
        "serve",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--refresh-interval",
        str(config.refresh_interval),
    ]


def menubar_program_arguments(config: AutostartConfig) -> list[str]:
    return [
        config.python_executable,
        str(config.resolved_usagectl_path),
        "menubar",
        "--interval",
        str(config.menubar_interval),
        "--dashboard-url",
        config.resolved_dashboard_url,
    ]


def _agent_path(config: AutostartConfig) -> str:
    """PATH for the LaunchAgent environment.

    launchd starts agents with a minimal PATH, which breaks adapters that
    shell out to CLIs (e.g. `claude`, `hermes`). Prepend the python executable's
    bin dir and the usual user/system bin dirs so those stay reachable.
    """
    python_bin = str(Path(config.python_executable).expanduser().parent)
    home = Path.home()
    entries = [
        python_bin,
        str(home / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    seen: set[str] = set()
    unique = [e for e in entries if not (e in seen or seen.add(e))]
    return ":".join(unique)


def _base_plist(config: AutostartConfig, label: str, program_arguments: list[str], log_name: str) -> dict:
    log_dir = config.resolved_log_dir
    return {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(Path(config.working_dir).expanduser()),
        "EnvironmentVariables": {"PATH": _agent_path(config)},
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / f"{log_name}.out.log"),
        "StandardErrorPath": str(log_dir / f"{log_name}.err.log"),
    }


def build_server_plist(config: AutostartConfig) -> dict:
    """LaunchAgent dict for the standalone FastAPI backend.

    Restarts on crash (unsuccessful exit) but stays stopped after a clean exit.
    """
    plist = _base_plist(config, config.server_label, server_program_arguments(config), "server")
    plist["KeepAlive"] = {"SuccessfulExit": False}
    return plist


def build_menubar_plist(config: AutostartConfig) -> dict:
    """LaunchAgent dict for the optional rumps menu bar app.

    Marked interactive because rumps needs the user's GUI (Aqua) session.
    Not kept alive: a crashed menu bar app should be restarted manually.
    """
    plist = _base_plist(config, config.menubar_label, menubar_program_arguments(config), "menubar")
    plist["ProcessType"] = "Interactive"
    plist["KeepAlive"] = False
    return plist


def render_plist(plist: dict) -> bytes:
    """Serialize a plist dict to XML bytes (always valid plist XML)."""
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)


def available_kinds(config: AutostartConfig) -> dict[str, dict]:
    return {
        "server": build_server_plist(config),
        "menubar": build_menubar_plist(config),
    }


def select_kinds(config: AutostartConfig, kind: str) -> dict[str, dict]:
    plists = available_kinds(config)
    if kind == "both":
        return plists
    return {kind: plists[kind]}


def write_plists(config: AutostartConfig, output_dir: str | Path, kind: str = "both") -> list[Path]:
    """Write the selected plist files into ``output_dir``; returns written paths.

    Creates the directory if needed. Never touches ~/Library/LaunchAgents
    unless the caller explicitly passes it, and never loads/enables anything.
    """
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for plist in select_kinds(config, kind).values():
        path = out / f"{plist['Label']}.plist"
        path.write_bytes(render_plist(plist))
        written.append(path)
    return written


# --- launchctl management (only runs on explicit user action) ----------------


def _default_launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _launchctl(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["launchctl", *args], text=True, capture_output=True, timeout=20)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def agent_status(config: AutostartConfig, launchagents_dir: str | Path | None = None) -> dict:
    """Read-only state of both agents: plist file presence + launchctl state."""
    directory = Path(launchagents_dir).expanduser() if launchagents_dir else _default_launchagents_dir()
    uid = os.getuid()
    agents = []
    for label in (config.server_label, config.menubar_label):
        plist_path = directory / f"{label}.plist"
        rc, out = _launchctl("print", f"gui/{uid}/{label}")
        loaded = rc == 0
        pid = state = None
        if loaded:
            m = re.search(r"^\s*pid = (\d+)", out, re.M)
            pid = int(m.group(1)) if m else None
            m = re.search(r"^\s*state = (\w+)", out, re.M)
            state = m.group(1) if m else None
        agents.append({
            "label": label,
            "plist": str(plist_path),
            "plist_exists": plist_path.exists(),
            "loaded": loaded,
            "pid": pid,
            "state": state,
        })
    return {"agents": agents, "installed": any(a["plist_exists"] for a in agents)}


def bootstrap_agent(config: AutostartConfig, label: str, launchagents_dir: str | Path | None = None) -> bool:
    """(Re)load a single agent's plist via launchctl. Explicit action only."""
    directory = Path(launchagents_dir).expanduser() if launchagents_dir else _default_launchagents_dir()
    uid = os.getuid()
    plist_path = directory / f"{label}.plist"
    _launchctl("bootout", f"gui/{uid}", str(plist_path))  # fine if not loaded
    rc, _ = _launchctl("bootstrap", f"gui/{uid}", str(plist_path))
    return rc == 0


def restart_agents(
    config: AutostartConfig,
    kind: str = "server",
    launchagents_dir: str | Path | None = None,
) -> dict:
    """Boot out and re-bootstrap installed agents. Explicit action only.

    Used to pick up config changes (a rotated auth password, a new provider)
    without hunting for launchctl invocations by hand. Only agents whose plist
    is already installed are touched; nothing is written or generated here.
    """
    directory = Path(launchagents_dir).expanduser() if launchagents_dir else _default_launchagents_dir()
    labels = {
        "server": [config.server_label],
        "menubar": [config.menubar_label],
        "both": [config.server_label, config.menubar_label],
    }.get(kind)
    if labels is None:
        raise ValueError(f"Unknown kind: {kind!r} (expected server, menubar or both)")
    restarted, missing, failed = [], [], []
    for label in labels:
        if not (directory / f"{label}.plist").exists():
            missing.append(label)
            continue
        if bootstrap_agent(config, label, launchagents_dir=directory):
            restarted.append(label)
        else:
            failed.append(label)
    return {"restarted": restarted, "missing": missing, "failed": failed}


def install_agents(config: AutostartConfig, launchagents_dir: str | Path | None = None) -> dict:
    """Write both plists into the LaunchAgents dir and (re)load the menubar agent.

    The server agent is never bootstrapped here: this is meant to be invoked
    from the dashboard, so a server is already running — bootstrapping another
    would fight over the port, and booting out the current one kills the very
    dashboard serving the request. With ``RunAtLoad`` set, the server plist
    takes effect at the next login.
    """
    directory = Path(launchagents_dir).expanduser() if launchagents_dir else _default_launchagents_dir()
    directory.mkdir(parents=True, exist_ok=True)
    written = write_plists(config, directory, kind="both")
    bootstrapped = bootstrap_agent(config, config.menubar_label, launchagents_dir=directory)
    return {
        "written": [str(p) for p in written],
        "menubar_bootstrapped": bootstrapped,
        "server_note": "server plist active at next login (current server left untouched)",
    }


def uninstall_agents(config: AutostartConfig, launchagents_dir: str | Path | None = None) -> dict:
    """Stop the menubar agent and remove both plists (disables login start).

    The running server process is left alive — booting it out would kill the
    dashboard serving the request — but without its plist it will not start
    at the next login.
    """
    directory = Path(launchagents_dir).expanduser() if launchagents_dir else _default_launchagents_dir()
    uid = os.getuid()
    menubar_plist = directory / f"{config.menubar_label}.plist"
    _launchctl("bootout", f"gui/{uid}", str(menubar_plist))  # fine if not loaded
    removed = []
    for label in (config.server_label, config.menubar_label):
        plist_path = directory / f"{label}.plist"
        if plist_path.exists():
            plist_path.unlink()
            removed.append(str(plist_path))
    return {"removed": removed, "server_note": "running server left alive until logout/restart"}

# SPDX-License-Identifier: MIT
"""LaunchAgent plist generation tests.

All filesystem writes go to tmp_path; nothing is loaded/enabled, no launchctl
is invoked, and no machine-specific state is touched. Generated XML is
validated by round-tripping through plistlib.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import usagectl
from usage_monitor_app import autostart


def _config(tmp_path, **overrides):
    defaults = dict(
        python_executable="/fake/python3",
        working_dir=str(tmp_path / "repo"),
        log_dir=str(tmp_path / "logs"),
    )
    defaults.update(overrides)
    return autostart.AutostartConfig(**defaults)


# --- server plist -----------------------------------------------------------


def test_server_plist_roundtrips_as_valid_xml(tmp_path):
    config = _config(tmp_path)
    raw = autostart.render_plist(autostart.build_server_plist(config))
    assert raw.startswith(b"<?xml")
    plist = plistlib.loads(raw)
    assert plist["Label"] == "com.usage-monitor.server"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["WorkingDirectory"] == str(tmp_path / "repo")
    assert plist["StandardOutPath"] == str(tmp_path / "logs" / "server.out.log")
    assert plist["StandardErrorPath"] == str(tmp_path / "logs" / "server.err.log")


def test_server_program_arguments_reflect_config(tmp_path):
    config = _config(tmp_path, port=8123, refresh_interval=60, host="0.0.0.0")
    args = autostart.build_server_plist(config)["ProgramArguments"]
    assert args[0] == "/fake/python3"
    assert args[1] == str(tmp_path / "repo" / "scripts" / "usagectl.py")
    assert args[2] == "serve"
    assert args[args.index("--port") + 1] == "8123"
    assert args[args.index("--host") + 1] == "0.0.0.0"
    assert args[args.index("--refresh-interval") + 1] == "60"


def test_server_usagectl_path_override(tmp_path):
    config = _config(tmp_path, usagectl_path="/opt/tools/usagectl.py")
    args = autostart.build_server_plist(config)["ProgramArguments"]
    assert args[1] == "/opt/tools/usagectl.py"


# --- menubar plist ----------------------------------------------------------


def test_menubar_plist_roundtrips_as_valid_xml(tmp_path):
    config = _config(tmp_path)
    plist = plistlib.loads(autostart.render_plist(autostart.build_menubar_plist(config)))
    assert plist["Label"] == "com.usage-monitor.menubar"
    assert plist["ProcessType"] == "Interactive"
    assert plist["KeepAlive"] is False
    args = plist["ProgramArguments"]
    assert args[2] == "menubar"
    assert args[args.index("--interval") + 1] == str(autostart.DEFAULT_MENUBAR_INTERVAL)
    assert args[args.index("--dashboard-url") + 1] == "http://127.0.0.1:9097"


def test_menubar_dashboard_url_explicit(tmp_path):
    config = _config(tmp_path, dashboard_url="http://localhost:1234", menubar_interval=42)
    args = autostart.build_menubar_plist(config)["ProgramArguments"]
    assert args[args.index("--dashboard-url") + 1] == "http://localhost:1234"
    assert args[args.index("--interval") + 1] == "42"


def test_dashboard_url_defaults_to_host_port(tmp_path):
    config = _config(tmp_path, host="127.0.0.1", port=7777)
    assert config.resolved_dashboard_url == "http://127.0.0.1:7777"


# --- writing files ----------------------------------------------------------


def test_write_plists_both_into_tmp_dir(tmp_path):
    config = _config(tmp_path)
    out = tmp_path / "agents"
    written = autostart.write_plists(config, out, kind="both")
    assert len(written) == 2
    names = {p.name for p in written}
    assert names == {
        "com.usage-monitor.server.plist",
        "com.usage-monitor.menubar.plist",
    }
    for path in written:
        plistlib.loads(path.read_bytes())  # raises if invalid


def test_write_plists_single_kind(tmp_path):
    config = _config(tmp_path)
    written = autostart.write_plists(config, tmp_path / "agents", kind="server")
    assert [p.name for p in written] == ["com.usage-monitor.server.plist"]


def test_custom_label_prefix(tmp_path):
    config = _config(tmp_path, label_prefix="com.example.usage")
    written = autostart.write_plists(config, tmp_path / "agents", kind="both")
    assert {p.stem for p in written} == {"com.example.usage.server", "com.example.usage.menubar"}
    plist = plistlib.loads(written[0].read_bytes())
    assert plist["Label"].startswith("com.example.usage.")


# --- CLI --------------------------------------------------------------------


def test_cli_autostart_writes_into_output_dir(tmp_path, capsys):
    out = tmp_path / "out"
    rc = usagectl.main(
        [
            "autostart",
            "--output-dir",
            str(out),
            "--python",
            "/fake/python3",
            "--working-dir",
            str(tmp_path),
            "--log-dir",
            str(tmp_path / "logs"),
            "--port",
            "9001",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr().out
    assert "wrote" in captured
    assert "launchctl bootstrap" in captured  # manual hint only; nothing executed
    server = plistlib.loads((out / "com.usage-monitor.server.plist").read_bytes())
    assert server["ProgramArguments"][server["ProgramArguments"].index("--port") + 1] == "9001"
    menubar = plistlib.loads((out / "com.usage-monitor.menubar.plist").read_bytes())
    assert menubar["Label"] == "com.usage-monitor.menubar"


def test_cli_autostart_prints_valid_xml_to_stdout(tmp_path, capsys):
    rc = usagectl.main(
        [
            "autostart",
            "--kind",
            "server",
            "--python",
            "/fake/python3",
            "--working-dir",
            str(tmp_path),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )
    assert rc == 0
    plist = plistlib.loads(capsys.readouterr().out.encode("utf-8"))
    assert plist["Label"] == "com.usage-monitor.server"


def test_cli_autostart_print_both_yields_two_documents(tmp_path, capsys):
    rc = usagectl.main(["autostart", "--working-dir", str(tmp_path), "--log-dir", str(tmp_path / "logs")])
    assert rc == 0
    out = capsys.readouterr().out
    docs = [d for d in out.split("<?xml") if d.strip()]
    assert len(docs) == 2
    labels = {plistlib.loads(("<?xml" + d).encode("utf-8"))["Label"] for d in docs}
    assert labels == {"com.usage-monitor.server", "com.usage-monitor.menubar"}


def test_cli_autostart_does_not_write_without_output_dir(tmp_path, capsys):
    usagectl.main(["autostart", "--working-dir", str(tmp_path), "--log-dir", str(tmp_path / "logs")])
    capsys.readouterr()
    assert list(tmp_path.rglob("*.plist")) == []


def test_cli_autostart_default_python_is_current_interpreter(tmp_path, capsys):
    usagectl.main(["autostart", "--kind", "server", "--working-dir", str(tmp_path), "--log-dir", str(tmp_path / "logs")])
    plist = plistlib.loads(capsys.readouterr().out.encode("utf-8"))
    assert plist["ProgramArguments"][0] == sys.executable


# --- packaged templates stay valid ------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["com.usage-monitor.server.plist", "com.usage-monitor.menubar.plist"],
)
def test_packaged_template_examples_are_valid_plists(name):
    path = Path(__file__).resolve().parents[1] / "templates" / "launchagents" / name
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] + ".plist" == name


# --- launchctl management (subprocess mocked) ---------------------------------


def _fake_launchctl(running_labels=()):
    def fake(*args):
        if args[0] == "print":
            label = args[1].rsplit("/", 1)[-1]
            if label in running_labels:
                return 0, "\tpid = 4242\n\tstate = running\n"
            return 1, "Could not find service"
        return 0, ""
    return fake


def test_agent_status_reports_loaded_and_files(tmp_path, monkeypatch):
    config = _config(tmp_path)
    (tmp_path / f"{config.server_label}.plist").write_text("x", encoding="utf-8")
    monkeypatch.setattr(autostart, "_launchctl", _fake_launchctl(running_labels={config.server_label}))
    status = autostart.agent_status(config, launchagents_dir=tmp_path)
    assert status["installed"] is True
    server, menubar = status["agents"]
    assert server["loaded"] is True and server["pid"] == 4242 and server["state"] == "running"
    assert server["plist_exists"] is True
    assert menubar["loaded"] is False and menubar["plist_exists"] is False


def test_restart_agents_reloads_only_installed_plists(tmp_path, monkeypatch):
    calls = []

    def fake(*args):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(autostart, "_launchctl", fake)
    config = _config(tmp_path)
    (tmp_path / f"{config.server_label}.plist").write_text("x", encoding="utf-8")

    result = autostart.restart_agents(config, kind="both", launchagents_dir=tmp_path)
    assert result["restarted"] == [config.server_label]
    assert result["missing"] == [config.menubar_label]
    assert result["failed"] == []
    # a restart is bootout followed by bootstrap, and only for the installed one
    assert [c[0] for c in calls] == ["bootout", "bootstrap"]
    assert all(config.server_label in c[2] for c in calls)


def test_restart_agents_reports_launchctl_failure(tmp_path, monkeypatch):
    def fake(*args):
        return (1, "Bootstrap failed") if args[0] == "bootstrap" else (0, "")

    monkeypatch.setattr(autostart, "_launchctl", fake)
    config = _config(tmp_path)
    (tmp_path / f"{config.server_label}.plist").write_text("x", encoding="utf-8")

    result = autostart.restart_agents(config, kind="server", launchagents_dir=tmp_path)
    assert result["failed"] == [config.server_label]
    assert result["restarted"] == []


def test_restart_agents_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        autostart.restart_agents(_config(tmp_path), kind="bogus", launchagents_dir=tmp_path)


def test_install_agents_writes_plists_and_bootstraps_menubar_only(tmp_path, monkeypatch):
    calls = []

    def fake(*args):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(autostart, "_launchctl", fake)
    config = _config(tmp_path)
    result = autostart.install_agents(config, launchagents_dir=tmp_path)
    assert result["menubar_bootstrapped"] is True
    bootstrapped = [c[2] for c in calls if c[0] == "bootstrap"]
    assert bootstrapped == [str(tmp_path / f"{config.menubar_label}.plist")]
    # server plist written but never bootstrapped/booted out from the dashboard
    assert (tmp_path / f"{config.server_label}.plist").exists()
    assert not any(c[0] == "bootstrap" and "server" in c[2] for c in calls)


def test_uninstall_agents_removes_plists_and_stops_menubar(tmp_path, monkeypatch):
    calls = []

    def fake(*args):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(autostart, "_launchctl", fake)
    config = _config(tmp_path)
    autostart.write_plists(config, tmp_path)
    result = autostart.uninstall_agents(config, launchagents_dir=tmp_path)
    assert len(result["removed"]) == 2
    assert not list(tmp_path.glob("*.plist"))
    booted_out = [c[2] for c in calls if c[0] == "bootout"]
    assert booted_out == [str(tmp_path / f"{config.menubar_label}.plist")]


def test_make_install_tray_reuses_existing_legacy_label_prefix(tmp_path):
    """Dry-run Makefile install picks the already-installed LaunchAgent label.

    This prevents the rename from com.hermes-usage-monitor to com.usage-monitor
    from creating a second tray instead of updating the active one.
    """
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "com.hermes-usage-monitor.server.plist").write_text("", encoding="utf-8")
    proc = subprocess.run(
        [
            "make",
            "-n",
            "install-tray",
            f"LAUNCHAGENTS_DIR={tmp_path}",
            "AGENT_PYTHON=/fake/python3",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    dry_run = proc.stdout + proc.stderr
    assert "--label-prefix \"com.hermes-usage-monitor\"" in dry_run
    assert f"{tmp_path}/com.hermes-usage-monitor.server.plist" in dry_run
    assert f"rm -f \"{tmp_path}/$prefix.server.plist\"" in dry_run  # stale duplicate cleanup

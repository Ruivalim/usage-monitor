#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Optional Hermes integration: a copy of the package installed by
# scripts/install-hermes-integration.sh. Only used when the standalone package
# is not importable from the repo/site-packages.
HERMES_HOME = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser()
INSTALLED_DASHBOARD = HERMES_HOME / 'plugins' / 'api-usage-monitor' / 'dashboard'

for candidate in (INSTALLED_DASHBOARD, REPO_ROOT):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    import usage_monitor_app.core as usage_monitor
except ImportError as exc:
    raise RuntimeError(
        f'Usage monitor package not found. Tried {REPO_ROOT} and {INSTALLED_DASHBOARD}. '
        "Run 'make setup' or execute from the repo."
    ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Standalone/provider-agnostic API usage and balance monitor')
    sub = parser.add_subparsers(dest='cmd')

    status = sub.add_parser('status', help='Fetch current provider status')
    status.add_argument('--json', action='store_true', help='print JSON instead of text')
    status.add_argument('--pretty', action='store_true', help='pretty-print JSON')
    status.add_argument('--no-persist', action='store_true', help='do not append snapshot')
    status.add_argument('--notify', action='store_true', help='send macOS notifications for new (deduped) alerts')
    status.add_argument('--snooze-seconds', type=int, default=None, help='override alert snooze window in seconds')

    latest = sub.add_parser('latest', help='Print latest persisted snapshot(s)')
    latest.add_argument('--limit', type=int, default=1)
    latest.add_argument('--pretty', action='store_true')

    menubar_cmd = sub.add_parser('menubar', help='Run macOS menu bar app (requires the rumps extra)')
    menubar_cmd.add_argument('--interval', type=int, default=None, help='auto-refresh interval in seconds; 0 disables (default: 300 or USAGE_MONITOR_MENUBAR_INTERVAL)')
    menubar_cmd.add_argument('--dashboard-url', default=None, help="URL opened by 'Open Dashboard' (default: http://127.0.0.1:9097 or USAGE_MONITOR_DASHBOARD_URL)")
    menubar_cmd.add_argument('--no-persist', action='store_true', help='do not append snapshots on refresh')

    serve = sub.add_parser('serve', help='Run standalone FastAPI dashboard')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=9097)
    serve.add_argument('--reload', action='store_true')
    serve.add_argument('--refresh-interval', type=int, default=None, help='background refresh interval in seconds; 0 disables scheduler (default: config.yaml intervals.refresh_seconds, 900)')

    autostart = sub.add_parser(
        'autostart',
        help='Generate macOS LaunchAgent plist files (prints or writes only; never loads/enables them)',
    )
    autostart.add_argument('--kind', choices=['server', 'menubar', 'both'], default='both', help='which agent(s) to generate (default: both)')
    autostart.add_argument('--output-dir', default=None, help='directory to write plist files into; omit to print XML to stdout')
    autostart.add_argument('--python', dest='python_executable', default=None, help='python executable for the agent (default: current interpreter)')
    autostart.add_argument('--working-dir', default=None, help='working directory of the agents (default: repo root)')
    autostart.add_argument('--usagectl', dest='usagectl_path', default=None, help='path to usagectl.py (default: <working-dir>/scripts/usagectl.py)')
    autostart.add_argument('--host', default='127.0.0.1', help='backend bind host (default: 127.0.0.1)')
    autostart.add_argument('--port', type=int, default=9097, help='backend port (default: 9097)')
    autostart.add_argument('--refresh-interval', type=int, default=None, help='backend scheduler interval in seconds; 0 disables (default: config.yaml intervals.refresh_seconds, 900)')
    autostart.add_argument('--log-dir', default=None, help='directory for stdout/stderr logs (default: $USAGE_MONITOR_HOME/logs)')
    autostart.add_argument('--dashboard-url', default=None, help='URL opened by the menubar app (default: http://<host>:<port>)')
    autostart.add_argument('--menubar-interval', type=int, default=None, help='menubar auto-refresh interval in seconds; 0 disables (default: config.yaml intervals.menubar_seconds, 300)')
    autostart.add_argument('--label-prefix', default='com.usage-monitor', help='launchd label prefix (default: com.usage-monitor)')

    restart = sub.add_parser(
        'restart',
        help='Restart the installed LaunchAgents so they pick up config changes',
    )
    restart.add_argument('--kind', choices=['server', 'menubar', 'both'], default='server', help='which agent(s) to restart (default: server)')
    restart.add_argument('--label-prefix', default='com.usage-monitor', help='launchd label prefix (default: com.usage-monitor)')

    codex_login = sub.add_parser(
        'codex-login',
        help='Sign in to OpenAI Codex / ChatGPT via device-code OAuth; store tokens in Keychain, update providers.yaml, restart agents',
    )
    codex_login.add_argument(
        '--service',
        default='api-usage-monitor/codex',
        help='Keychain service name (default: api-usage-monitor/codex)',
    )
    codex_login.add_argument(
        '--account',
        default='default',
        help='Keychain account name (default: default; use a distinct value per ChatGPT account)',
    )
    codex_login.add_argument(
        '--provider-id',
        default='codex',
        help='providers.yaml id to write/update (default: codex)',
    )
    codex_login.add_argument(
        '--name',
        default='Codex',
        help='display name for the provider entry (default: Codex)',
    )
    codex_login.add_argument(
        '--no-keychain',
        action='store_true',
        help='do not write Keychain (prints only that login succeeded)',
    )
    codex_login.add_argument(
        '--no-config',
        action='store_true',
        help='do not add/update the providers.yaml entry',
    )
    codex_login.add_argument(
        '--no-restart',
        action='store_true',
        help='do not restart LaunchAgents after login',
    )
    codex_login.add_argument(
        '--restart-kind',
        choices=['server', 'menubar', 'both'],
        default='both',
        help='which LaunchAgents to restart (default: both)',
    )

    auth = sub.add_parser('auth', help='Manage dashboard/API basic auth stored in config.yaml')
    auth_sub = auth.add_subparsers(dest='auth_cmd')
    auth_enable = auth_sub.add_parser('enable', help='Enable basic auth (generates a password unless one is given)')
    auth_enable.add_argument('--username', default=None, help='username to require (default: local)')
    auth_enable.add_argument('--password', default=None, help='password to hash; avoid on shared shells (leaks to history)')
    auth_enable.add_argument('--password-stdin', action='store_true', help='read the password from stdin instead of --password')
    auth_enable.add_argument('--config', default=None, help='config.yaml path (default: $USAGE_MONITOR_HOME/config.yaml)')
    auth_disable = auth_sub.add_parser('disable', help='Disable basic auth (keeps the stored username/hash)')
    auth_disable.add_argument('--config', default=None, help='config.yaml path')
    auth_status = auth_sub.add_parser('status', help='Show whether auth is enabled and where it comes from')
    auth_status.add_argument('--config', default=None, help='config.yaml path')
    auth_hash = auth_sub.add_parser('hash', help='Print a password hash for USAGE_MONITOR_AUTH_PASSWORD_HASH')
    auth_hash.add_argument('--password', default=None, help='password to hash; omit to read from stdin')

    install_cli = sub.add_parser('install-cli', help='Install the standalone `usagemon` wrapper into ~/.local/bin (idempotent)')
    install_cli.add_argument('--repo', default=None, help='path to repo root; defaults to <script>/.. or cwd')
    install_cli.add_argument('--bin-dir', default=None, help='directory to install into (default: ~/.local/bin)')

    install = sub.add_parser('install', help='Optional Hermes integration: install skill + plugin + Desktop UI + adapters into ~/.hermes (idempotent)')
    install.add_argument('--repo', default=None, help='path to repo root; defaults to <script>/.. or cwd')
    install.add_argument('--skip-plugin', action='store_true', help='skip hermes plugins enable step')

    args = parser.parse_args(argv)
    if args.cmd is None:
        # no subcommand: behave like `status`, with that parser's defaults populated
        args = parser.parse_args(['status'])
    cmd = args.cmd

    if cmd == 'status':
        persist = not args.no_persist
        snap = usage_monitor.collect_status(persist=persist)
        if args.notify:
            from usage_monitor_app import alerts as usage_alerts
            new = usage_alerts.process_snapshot(snap, snooze_seconds=args.snooze_seconds)
            if not args.json:
                print(f'alerts: {len(new)} new' if new else 'alerts: no new alerts')
        if args.json:
            print(json.dumps(usage_monitor._to_plain(snap), indent=2 if args.pretty else None, ensure_ascii=False, sort_keys=True))
        else:
            print(usage_monitor.render_text(snap))
        return 0

    if cmd == 'latest':
        print(json.dumps({'snapshots': usage_monitor.latest_snapshot(args.limit)}, indent=2 if args.pretty else None, ensure_ascii=False, sort_keys=True))
        return 0

    if cmd == 'menubar':
        from usage_monitor_app import menubar
        menubar_argv = []
        if args.interval is not None:
            menubar_argv += ['--interval', str(args.interval)]
        if args.dashboard_url:
            menubar_argv += ['--dashboard-url', args.dashboard_url]
        if args.no_persist:
            menubar_argv.append('--no-persist')
        return menubar.main(menubar_argv)

    if cmd == 'serve':
        import uvicorn
        if args.refresh_interval is not None:
            os.environ['USAGE_MONITOR_REFRESH_INTERVAL'] = str(args.refresh_interval)
        uvicorn.run('usage_monitor_app.web:app', host=args.host, port=args.port, reload=args.reload)
        return 0

    if cmd == 'autostart':
        from usage_monitor_app import autostart as usage_autostart
        from usage_monitor_app.config import load_app_config

        app_config = load_app_config()
        config = usage_autostart.AutostartConfig(
            python_executable=args.python_executable or sys.executable,
            working_dir=args.working_dir or str(REPO_ROOT),
            usagectl_path=args.usagectl_path,
            host=args.host,
            port=args.port,
            refresh_interval=args.refresh_interval if args.refresh_interval is not None else app_config.intervals.refresh_seconds,
            log_dir=args.log_dir,
            dashboard_url=args.dashboard_url,
            menubar_interval=args.menubar_interval if args.menubar_interval is not None else app_config.intervals.menubar_seconds,
            label_prefix=args.label_prefix,
        )
        plists = usage_autostart.select_kinds(config, args.kind)
        if args.output_dir:
            written = usage_autostart.write_plists(config, args.output_dir, kind=args.kind)
            for path in written:
                print(f'wrote {path}')
            print()
            print('Nothing was loaded or enabled. To activate manually on macOS:')
            print(f'  mkdir -p "{config.resolved_log_dir}"')
            for path in written:
                print(f'  cp "{path}" ~/Library/LaunchAgents/')
                print(f'  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/{path.name}"')
            print('To stop later: launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/<file>.plist')
        else:
            for plist in plists.values():
                sys.stdout.write(usage_autostart.render_plist(plist).decode('utf-8'))
        return 0

    if cmd == 'restart':
        from usage_monitor_app import autostart as usage_autostart

        config = usage_autostart.AutostartConfig(
            python_executable=sys.executable,
            working_dir=str(REPO_ROOT),
            label_prefix=args.label_prefix,
        )
        result = usage_autostart.restart_agents(config, kind=args.kind)
        for label in result['restarted']:
            print(f'restarted {label}')
        for label in result['missing']:
            print(f'not installed, skipped: {label} (run `make install-tray` first)', file=sys.stderr)
        for label in result['failed']:
            print(f'failed to restart {label}', file=sys.stderr)
        if result['failed']:
            return 1
        if not result['restarted']:
            return 2
        return 0

    if cmd == 'codex-login':
        from usage_monitor_app import codex as usage_codex

        try:
            usage_codex.device_code_login(
                store_keychain=not args.no_keychain,
                service=args.service,
                account=args.account,
            )
        except SystemExit as exc:
            return int(exc.code or 1)
        except Exception as exc:
            print(f'codex-login failed: {exc}', file=sys.stderr)
            return 1

        if not args.no_config and not args.no_keychain:
            try:
                path, action = usage_codex.ensure_providers_entry(
                    service=args.service,
                    account=args.account,
                    provider_id=args.provider_id,
                    name=args.name,
                )
                print(f'providers.yaml {action}: {path} (id={args.provider_id})')
            except Exception as exc:
                print(f'warning: could not update providers.yaml: {exc}', file=sys.stderr)
        elif args.no_config or args.no_keychain:
            print('Skipped providers.yaml update (--no-config or --no-keychain).')

        if not args.no_restart:
            from usage_monitor_app import autostart as usage_autostart

            config = usage_autostart.AutostartConfig(
                python_executable=sys.executable,
                working_dir=str(REPO_ROOT),
            )
            result = usage_autostart.restart_agents(config, kind=args.restart_kind)
            for label in result['restarted']:
                print(f'restarted {label}')
            for label in result['missing']:
                print(f'not installed, skipped: {label}', file=sys.stderr)
            for label in result['failed']:
                print(f'failed to restart {label}', file=sys.stderr)
            if result['failed']:
                return 1
            if not result['restarted'] and result['missing']:
                print('No LaunchAgents installed — run `usagemon status` or `make install-tray` if you use the tray/server.')
        return 0

    if cmd == 'auth':
        return _auth_command(args, parser=auth)

    if cmd == 'install-cli':
        try:
            repo = _resolve_repo(args.repo) if args.repo else REPO_ROOT
            wrapper = install_cli_wrapper(repo, bin_dir=Path(args.bin_dir).expanduser() if args.bin_dir else None)
        except Exception as exc:
            print(f'Install failed: {exc}', file=sys.stderr)
            return 1
        print(f'       → {wrapper}')
        print()
        print('Make sure that directory is on your PATH, then run: usagemon status')
        return 0

    if cmd == 'install':
        try:
            repo = _resolve_repo(args.repo) if args.repo else REPO_ROOT
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            result = install_skill(repo, skip_plugin=args.skip_plugin)
        except Exception as exc:
            print(f"Install failed: {exc}", file=sys.stderr)
            return 1
        for section, paths in sorted(result.items()):
            if section == "warnings":
                for w in paths:
                    print(f"  ⚠  {w}")
            elif section == "plugin":
                for p in paths:
                    print(f"       {p}")
            else:
                for p in paths:
                    print(f"       → {p}")
        print()
        print("Done! Hermes integration installed.")
        print()
        print("The standalone install is untouched. Quick test:")
        print(f"  {repo / '.venv' / 'bin' / 'python'} {repo / 'scripts' / 'usagectl.py'} status")
        return 0

    parser.print_help()
    return 2


def _auth_command(args, *, parser) -> int:
    from usage_monitor_app import config as usage_config

    sub_cmd = getattr(args, 'auth_cmd', None)
    if sub_cmd is None:
        parser.print_help()
        return 2
    config_path = Path(args.config).expanduser() if getattr(args, 'config', None) else None

    if sub_cmd == 'enable':
        password = args.password
        if args.password_stdin:
            password = sys.stdin.readline().rstrip('\n')
        path, secret = usage_config.set_auth(
            config_path,
            username=args.username or usage_config.DEFAULT_USERNAME,
            password=password,
        )
        print(f'Basic auth enabled in {path}')
        print(f'  username: {args.username or usage_config.DEFAULT_USERNAME}')
        if not args.password and not args.password_stdin:
            print(f'  password: {secret}')
            print('  (generated once — store it now, only the hash is saved)')
        print('Restart the server for the change to take effect.')
        return 0

    if sub_cmd == 'disable':
        path = usage_config.disable_auth(config_path)
        print(f'Basic auth disabled in {path}')
        print('Restart the server for the change to take effect.')
        return 0

    if sub_cmd == 'status':
        cfg = usage_config.load_app_config(config_path)
        print(f'config file : {cfg.path}')
        print(f'auth enabled: {cfg.auth.enabled}')
        print(f'credential  : {"password_hash" if cfg.auth.password_hash else "password (plaintext)" if cfg.auth.password else "none"}')
        if cfg.auth.enabled and not cfg.auth.configured:
            print('WARNING: auth is enabled but no credential is configured; the server refuses to start.')
        return 0

    if sub_cmd == 'hash':
        password = args.password if args.password else sys.stdin.readline().rstrip('\n')
        if not password:
            print('empty password', file=sys.stderr)
            return 1
        print(usage_config.hash_password(password))
        return 0

    parser.print_help()
    return 2


def _resolve_repo(flag: str | None) -> Path:
    if flag:
        p = Path(flag).expanduser().resolve()
    elif REPO_ROOT.exists():
        p = REPO_ROOT
    else:
        p = Path.cwd()
    if not p.is_dir():
        raise FileNotFoundError(f"repo root not found: {p}")
    return p


def install_cli_wrapper(repo: Path | None = None, *, bin_dir: Path | None = None) -> Path:
    """Write ``usagemon`` into ``~/.local/bin``, bound to this repo checkout.

    Standalone by design: the wrapper pins the repo's own venv (falling back to
    whatever ``python3`` is on PATH) and calls this repo's ``usagectl.py``. No
    external install is consulted.
    """
    src = repo or _resolve_repo(None)
    target_dir = Path(bin_dir).expanduser() if bin_dir else Path.home() / ".local" / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = target_dir / "usagemon"
    venv_python = src / ".venv" / "bin" / "python"
    usagectl = src / "scripts" / "usagectl.py"
    wrapper_path.write_text(
        "#!/usr/bin/env bash\n"
        "# usagemon — API Usage Monitor CLI (status, serve, menubar, autostart, latest)\n"
        "# Generated by 'usagectl install-cli'; regenerate after moving the checkout.\n"
        f'PY="{venv_python}"\n'
        '[ -x "${PY}" ] || PY="$(command -v python3)"\n'
        f'exec "${{PY}}" "{usagectl}" "$@"\n'
    )
    wrapper_path.chmod(0o755)
    return wrapper_path


def install_skill(repo: Path | None = None, *, skip_plugin: bool = False) -> dict[str, list[str]]:
    src = repo or _resolve_repo(None)
    plugin_name = "api-usage-monitor"
    plugin_root_dst = HERMES_HOME / "plugins" / plugin_name
    plugin_dst = plugin_root_dst / "dashboard"
    desktop_plugin_dst = HERMES_HOME / "desktop-plugins" / plugin_name
    skill_dst = HERMES_HOME / "skills" / "productivity" / "api-usage-monitoring"
    adapters_dst = HERMES_HOME / "usage" / "adapters"
    usage_home = HERMES_HOME / "usage"
    launchagents_dst = usage_home / "launchagents"

    installed: dict[str, list[str]] = {}

    # 1. Plugin core
    plugin_dst.mkdir(parents=True, exist_ok=True)
    (plugin_dst / "dist").mkdir(parents=True, exist_ok=True)
    plugin_files = [
        # plugin.yaml + __init__.py belong at the plugin ROOT, not in dashboard/:
        # Hermes only discovers a directory holding both, and discovery is what
        # lets `hermes plugins enable` add the plugin to plugins.enabled — the
        # gate the web server checks before mounting dashboard/plugin_api.py.
        (src / "plugin" / "agent" / "plugin.yaml", plugin_root_dst / "plugin.yaml"),
        (src / "plugin" / "agent" / "__init__.py", plugin_root_dst / "__init__.py"),
        (src / "plugin" / "manifest.json", plugin_dst / "manifest.json"),
        (src / "plugin" / "usage_monitor.py", plugin_dst / "usage_monitor.py"),
        (src / "plugin" / "plugin_api.py", plugin_dst / "plugin_api.py"),
        # Placeholder for manifest.json's `entry`; the real UI is the desktop plugin.
        (src / "plugin" / "dist" / "index.js", plugin_dst / "dist" / "index.js"),
    ]
    for src_file, dst_file in plugin_files:
        if src_file.is_file():
            shutil.copy2(src_file, dst_file)
            installed.setdefault("plugin_core", []).append(str(dst_file))
    dashboard_src = src / "usage_monitor_app"
    dashboard_dst = plugin_dst / "usage_monitor_app"
    if dashboard_src.is_dir():
        if dashboard_dst.exists():
            shutil.rmtree(dashboard_dst)
        shutil.copytree(dashboard_src, dashboard_dst)
        installed.setdefault("plugin_core", []).append(str(dashboard_dst))

    # 2. Desktop plugin UI (statusbar chip + pane + palette command)
    desktop_src = src / "plugin" / "desktop" / "plugin.js"
    if desktop_src.is_file():
        desktop_plugin_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(desktop_src, desktop_plugin_dst / "plugin.js")
        installed.setdefault("desktop_plugin", []).append(str(desktop_plugin_dst / "plugin.js"))

    # 3. External adapters + example configs
    adapters_dst.mkdir(parents=True, exist_ok=True)
    usage_home.mkdir(parents=True, exist_ok=True)
    examples_src = src / "examples"
    for example_name, dst_name in (("providers.yaml", "providers.example.yaml"), ("prices.yaml", "prices.example.yaml")):
        dst = usage_home / dst_name
        if not dst.exists() and (examples_src / example_name).is_file():
            shutil.copy2(examples_src / example_name, dst)
            installed.setdefault("examples", []).append(str(dst))
    adapters_src = src / "plugin" / "adapters"
    if adapters_src.is_dir():
        for adapter in adapters_src.glob("*.py"):
            dst = adapters_dst / adapter.name
            shutil.copy2(adapter, dst)
            installed.setdefault("adapters", []).append(str(dst))

    # 4. CLI script
    scripts_dst = skill_dst / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    usagectl_src = src / "scripts" / "usagectl.py"
    dst_script = scripts_dst / "usagectl.py"
    shutil.copy2(usagectl_src, dst_script)
    dst_script.chmod(0o755)
    installed.setdefault("script", []).append(str(dst_script))

    # 5. SKILL.md
    skill_dst.mkdir(parents=True, exist_ok=True)
    skill_src = src / "skills" / "api-usage-monitoring" / "SKILL.md"
    if skill_src.is_file():
        shutil.copy2(skill_src, skill_dst / "SKILL.md")
        installed.setdefault("skill", []).append(str(skill_dst / "SKILL.md"))

    # 6. LaunchAgent templates
    templates_src = src / "templates" / "launchagents"
    if templates_src.is_dir():
        launchagents_dst.mkdir(parents=True, exist_ok=True)
        for tmpl in templates_src.glob("*"):
            dst = launchagents_dst / tmpl.name
            if tmpl.is_file():
                shutil.copy2(tmpl, dst)
                installed.setdefault("launchagents", []).append(str(dst))

    # 7. Enable plugin
    if not skip_plugin:
        try:
            subprocess.run(["hermes", "plugins", "enable", plugin_name], capture_output=True, text=True, timeout=15)
            installed.setdefault("plugin", [f"hermes plugins enable {plugin_name}"])
        except Exception as exc:
            installed.setdefault("warnings", []).append(f"Could not enable plugin: {exc}")

    return installed


if __name__ == '__main__':
    raise SystemExit(main())

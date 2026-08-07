# SPDX-License-Identifier: MIT
"""
Google Antigravity (`agy` CLI) plan quota — via its local language-server RPC.

Antigravity exposes no `usage` subcommand and no `/usage` slash command
(the binary only registers SLASH_COMMAND_TYPE_SKILL and _SYSTEM).  What it
does expose, while running, is an unauthenticated Connect RPC on loopback:

    POST http://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/GetUserStatus

whose response carries `cascadeModelConfigData.clientModelConfigs[].quotaInfo`
with a `remainingFraction` and a `resetTime` per model — the 7-day plan window.

`agy` is a TUI and exits immediately without a controlling terminal, so this
adapter starts it on a pty, waits for the RPC to answer, reads the quota, and
kills the child.  It never touches an `agy` you started yourself.

Two ports are opened; only one speaks plaintext HTTP (the other is TLS and
just fails the probe), so every listener is tried and the first usable one wins.

A long-lived `agy` keeps serving the values it fetched at boot, so this adapter
spawns its own instance by default rather than reading yours.  Results are
cached (default 5 min) to keep the monitor's poll loop from spawning `agy`
every cycle.

Environment variables:
  USAGE_MONITOR_AGY_BIN         Path to the `agy` binary (default: PATH lookup)
  USAGE_MONITOR_AGY_TIMEOUT     Seconds to wait for quota data (default: 30)
  USAGE_MONITOR_AGY_SETTLE      Extra settle seconds after first read (default: 1.0)
  USAGE_MONITOR_AGY_CACHE_TTL   Cache lifetime in seconds (default: 300, 0 disables)
  USAGE_MONITOR_AGY_CACHE       Cache file path
  USAGE_MONITOR_AGY_REUSE       "1" reads a running `agy` instead of spawning
                                (faster, but the values may be stale)
  USAGE_MONITOR_AGY_WARN_PCT    Warn below this remaining percent (default: 15)
"""

from __future__ import annotations

import json
import os
import pty
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROVIDER_ID = "antigravity"
DEFAULT_LABEL = "Antigravity (agy)"
RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"

DEFAULT_TIMEOUT = 45.0
DEFAULT_SETTLE = 1.0
DEFAULT_CACHE_TTL = 300.0
DEFAULT_WARN_PCT = 15.0
POLL_INTERVAL = 0.4


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _cache_path() -> Path:
    override = os.environ.get("USAGE_MONITOR_AGY_CACHE", "").strip()
    if override:
        return Path(override).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home).expanduser() / "usagemon" / "cache" / "antigravity.json"


def _read_cache(ttl: float) -> Optional[dict[str, Any]]:
    if ttl <= 0:
        return None
    path = _cache_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    if time.time() - stat.st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _listen_ports(pid: int) -> list[str]:
    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    ports = set()
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) > 8 and ":" in parts[8]:
            ports.add(parts[8].rsplit(":", 1)[-1])
    return sorted(ports)


def _get_user_status(port: str, timeout: float = 3.0) -> Optional[dict[str, Any]]:
    """POST an empty Connect request; None when this port is not the plain-HTTP one."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{RPC_PATH}",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _has_quota(payload: Optional[dict[str, Any]]) -> bool:
    if not payload:
        return False
    configs = (
        (payload.get("userStatus") or {})
        .get("cascadeModelConfigData", {})
        .get("clientModelConfigs", [])
    )
    return any((c.get("quotaInfo") or {}).get("remainingFraction") is not None for c in configs)


def _running_agy_pids() -> list[int]:
    try:
        proc = subprocess.run(["pgrep", "-x", "agy"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    return [int(x) for x in proc.stdout.split() if x.isdigit()]


def _first_quota_response(pid: int) -> Optional[dict[str, Any]]:
    for port in _listen_ports(pid):
        payload = _get_user_status(port)
        if _has_quota(payload):
            return payload
    return None


def _fetch_from_running() -> Optional[dict[str, Any]]:
    for pid in _running_agy_pids():
        payload = _first_quota_response(pid)
        if payload is not None:
            return payload
    return None


def _fetch_from_spawn(binary: str, timeout: float, settle: float) -> Optional[dict[str, Any]]:
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    env = {**os.environ, "TERM": "xterm-256color"}
    proc = subprocess.Popen(
        [binary],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        env=env,
    )
    os.close(slave)
    try:
        deadline = time.time() + timeout
        payload = None
        while time.time() < deadline:
            _drain(master)  # the TUI keeps painting; a full pty buffer would stall it
            if proc.poll() is not None:
                return None
            payload = _first_quota_response(proc.pid)
            if payload is not None:
                break
            time.sleep(POLL_INTERVAL)
        if payload is None:
            return None
        # Values measured stable from the first successful read, but re-read
        # once after a short settle so a slow refresh cannot hand back defaults.
        if settle > 0:
            time.sleep(settle)
            _drain(master)
            refreshed = _first_quota_response(proc.pid)
            if refreshed is not None:
                payload = refreshed
        return payload
    finally:
        _drain(master)
        _terminate(proc)
        try:
            os.close(master)
        except OSError:
            pass


def _drain(master: int) -> None:
    """Keep reading the TUI's output so it never blocks on a full pty buffer."""
    try:
        while os.read(master, 65536):
            pass
    except Exception:
        pass


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _family(model_label: str) -> str:
    """'Gemini 3.1 Pro (High)' -> 'Gemini'; 'GPT-OSS 120B (Medium)' -> 'GPT-OSS'."""
    return (model_label.split() or ["model"])[0]


def _pools(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Group models sharing a quota bucket into one window each."""
    configs = (
        (payload.get("userStatus") or {})
        .get("cascadeModelConfigData", {})
        .get("clientModelConfigs", [])
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for conf in configs:
        quota = conf.get("quotaInfo") or {}
        fraction = quota.get("remainingFraction")
        if fraction is None:
            continue
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            continue
        reset = str(quota.get("resetTime") or "")
        key = (f"{fraction:.6f}", reset)
        bucket = grouped.setdefault(
            key, {"fraction": fraction, "reset": reset, "models": [], "families": []}
        )
        label = str(conf.get("label") or "model")
        bucket["models"].append(label)
        family = _family(label)
        if family not in bucket["families"]:
            bucket["families"].append(family)
    pools = list(grouped.values())
    pools.sort(key=lambda p: p["fraction"])
    return pools


def _window_from_pool(pool: dict[str, Any]) -> dict[str, Any]:
    remaining = pool["fraction"] * 100
    models = pool["models"]
    detail = ", ".join(models[:3])
    if len(models) > 3:
        detail += f" +{len(models) - 3} more"
    return {
        "label": " / ".join(pool["families"]) or "Plan quota",
        "used_percent": max(0.0, 100.0 - remaining),
        "remaining_percent": remaining,
        "reset_at": pool["reset"] or None,
        "detail": detail,
    }


def _build_result(payload: dict[str, Any], source: str) -> dict[str, Any]:
    user_status = payload.get("userStatus") or {}
    tier = user_status.get("userTier") or {}
    plan_status = user_status.get("planStatus") or {}
    plan_info = plan_status.get("planInfo") or {}

    tier_name = str(tier.get("name") or plan_info.get("planName") or "").strip()
    label = f"Antigravity ({tier_name})" if tier_name else DEFAULT_LABEL

    pools = _pools(payload)
    windows = [_window_from_pool(p) for p in pools]

    details: list[str] = []
    if tier_name:
        tier_desc = str(tier.get("description") or "").strip()
        details.append(f"Plan: {tier_name}" + (f" — {tier_desc}" if tier_desc else ""))
    prompt_credits = plan_status.get("availablePromptCredits")
    flow_credits = plan_status.get("availableFlowCredits")
    if prompt_credits is not None or flow_credits is not None:
        details.append(
            f"Credits available: {prompt_credits or 0} prompt / {flow_credits or 0} flow"
        )
    email = str(user_status.get("email") or "").strip()
    if email:
        details.append(f"Account: {email}")

    warn_pct = _env_float("USAGE_MONITOR_AGY_WARN_PCT", DEFAULT_WARN_PCT)
    remaining_values = [p["fraction"] * 100 for p in pools]
    status = "ok"
    message = None
    if not windows:
        status = "unavailable"
        message = "agy answered but reported no quota windows"
    elif any(r <= 0 for r in remaining_values):
        status = "quota_exhausted"
        message = "One or more model pools are out of plan quota"
    elif any(r < warn_pct for r in remaining_values):
        status = "warning"
        message = f"A model pool is below {warn_pct:.0f}% remaining"

    return {
        "id": PROVIDER_ID,
        "label": label,
        "status": status,
        "source": source,
        "windows": windows,
        "details": details,
        "message": message,
    }


def check() -> dict[str, Any]:
    def unavailable(msg: str) -> dict[str, Any]:
        return {
            "id": PROVIDER_ID,
            "label": DEFAULT_LABEL,
            "status": "unavailable",
            "source": "agy_rpc",
            "message": msg,
        }

    cache_ttl = _env_float("USAGE_MONITOR_AGY_CACHE_TTL", DEFAULT_CACHE_TTL)
    cached = _read_cache(cache_ttl)
    if cached is not None:
        result = _build_result(cached, "agy_rpc (cached)")
        age = int(time.time() - _cache_path().stat().st_mtime)
        result["details"].append(f"Cached {age}s ago")
        return result

    binary = os.environ.get("USAGE_MONITOR_AGY_BIN", "").strip() or shutil.which("agy")
    reuse = os.environ.get("USAGE_MONITOR_AGY_REUSE", "").strip() == "1"

    payload = None
    source = "agy_rpc"
    if reuse:
        payload = _fetch_from_running()
        if payload is not None:
            source = "agy_rpc (running instance)"

    if payload is None:
        if not binary:
            return unavailable("`agy` binary not found (set USAGE_MONITOR_AGY_BIN)")
        if not Path(binary).exists():
            return unavailable(f"`agy` binary not found at {binary}")
        timeout = _env_float("USAGE_MONITOR_AGY_TIMEOUT", DEFAULT_TIMEOUT)
        settle = _env_float("USAGE_MONITOR_AGY_SETTLE", DEFAULT_SETTLE)
        try:
            payload = _fetch_from_spawn(binary, timeout, settle)
        except Exception as exc:
            return unavailable(f"Failed to query agy: {str(exc)[:200]}")
        if payload is None:
            return unavailable(
                f"agy did not report quota data within {timeout:.0f}s "
                f"(RPC timeout — not the same as 0% plan quota; try USAGE_MONITOR_AGY_TIMEOUT=60 "
                f"or USAGE_MONITOR_AGY_REUSE=1 with agy already open)"
            )

    _write_cache(payload)
    return _build_result(payload, source)


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))

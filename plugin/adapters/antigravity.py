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

Options (providers.yaml entry and/or env; YAML wins):

  bin / agy_bin                 USAGE_MONITOR_AGY_BIN
  timeout                       USAGE_MONITOR_AGY_TIMEOUT   (default 45)
  settle                        USAGE_MONITOR_AGY_SETTLE    (default 1.0)
  cache_ttl                     USAGE_MONITOR_AGY_CACHE_TTL (default 300; 0 disables)
  cache / cache_path            USAGE_MONITOR_AGY_CACHE
  reuse / reuse_running         USAGE_MONITOR_AGY_REUSE     (true/1)
  warn_percent / warn_pct       USAGE_MONITOR_AGY_WARN_PCT  (default 15)

Example::

  - id: antigravity
    name: Antigravity
    type: antigravity
    timeout: 60
    reuse: true
    warn_percent: 15
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


def _plog():
    try:
        from usage_monitor_app import plog as _p

        return _p
    except Exception:
        return None


def _log(level: str, msg: str, **extra: Any) -> None:
    p = _plog()
    if p is None:
        return
    getattr(p, level, p.info)(msg, **extra)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _conf_float(conf: dict[str, Any], *keys: str, env: str, default: float) -> float:
    for key in keys:
        if key in conf and conf[key] is not None:
            try:
                return float(conf[key])
            except (TypeError, ValueError):
                pass
    return _env_float(env, default)


def _conf_str(conf: dict[str, Any], *keys: str, env: str = "", default: str = "") -> str:
    for key in keys:
        raw = conf.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    if env:
        return os.environ.get(env, "").strip() or default
    return default


def _conf_bool(conf: dict[str, Any], *keys: str, env: str = "", default: bool = False) -> bool:
    for key in keys:
        if key in conf and conf[key] is not None:
            val = conf[key]
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("1", "true", "yes", "on")
    if env:
        return os.environ.get(env, "").strip() == "1"
    return default


def _cache_path(conf: Optional[dict[str, Any]] = None) -> Path:
    conf = conf or {}
    override = _conf_str(conf, "cache", "cache_path", env="USAGE_MONITOR_AGY_CACHE")
    if override:
        return Path(override).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home).expanduser() / "usagemon" / "cache" / "antigravity.json"


def _read_cache(ttl: float, conf: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if ttl <= 0:
        return None
    path = _cache_path(conf)
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


def _write_cache(payload: dict[str, Any], conf: Optional[dict[str, Any]] = None) -> None:
    path = _cache_path(conf)
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
    url = f"http://127.0.0.1:{port}{RPC_PATH}"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            has_q = _has_quota(body)
            _log("debug", "rpc ok", port=port, has_quota=has_q, shape=_payload_shape(body) if not has_q else None)
            return body
    except Exception as exc:
        _log("debug", "rpc fail", port=port, error=str(exc)[:160])
        return None


def _model_configs(payload: Optional[dict[str, Any]]) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    user_status = payload.get("userStatus") or payload.get("user_status") or {}
    if not isinstance(user_status, dict):
        return []
    cascade = (
        user_status.get("cascadeModelConfigData")
        or user_status.get("cascade_model_config_data")
        or {}
    )
    if not isinstance(cascade, dict):
        return []
    configs = cascade.get("clientModelConfigs") or cascade.get("client_model_configs") or []
    return configs if isinstance(configs, list) else []


def _quota_fraction(quota: dict[str, Any]) -> Any:
    """Return remaining fraction in 0–1 units, or None if no quota block.

    When plan quota is exhausted, Antigravity often **omits** remainingFraction
    and only returns ``resetTime``. That case is treated as 0 remaining (not
    "missing quota data").
    """
    if not isinstance(quota, dict) or not quota:
        return None
    for key in ("remainingFraction", "remaining_fraction"):
        if key in quota and quota[key] is not None:
            try:
                return float(quota[key])
            except (TypeError, ValueError):
                pass
    for key in ("remainingPercent", "remaining_percent"):
        if key in quota and quota[key] is not None:
            try:
                return float(quota[key]) / 100.0
            except (TypeError, ValueError):
                pass
    # Exhausted windows: only resetTime present
    if quota.get("resetTime") or quota.get("reset_time"):
        return 0.0
    return None


def _has_quota(payload: Optional[dict[str, Any]]) -> bool:
    if not payload:
        return False
    for conf in _model_configs(payload):
        if not isinstance(conf, dict):
            continue
        quota = conf.get("quotaInfo") or conf.get("quota_info") or {}
        if isinstance(quota, dict) and quota:
            # Any quotaInfo (even reset-only) is enough to treat the RPC as ready.
            return True
    return False


def _payload_shape(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Compact shape summary for debug logs (no PII beyond structural keys)."""
    if not isinstance(payload, dict):
        return {"kind": type(payload).__name__}
    us = payload.get("userStatus") or payload.get("user_status")
    shape: dict[str, Any] = {"top_keys": list(payload.keys())[:20]}
    if isinstance(us, dict):
        shape["userStatus_keys"] = list(us.keys())[:30]
        cascade = us.get("cascadeModelConfigData") or us.get("cascade_model_config_data")
        if isinstance(cascade, dict):
            shape["cascade_keys"] = list(cascade.keys())[:20]
            configs = cascade.get("clientModelConfigs") or cascade.get("client_model_configs") or []
            shape["model_configs"] = len(configs) if isinstance(configs, list) else 0
            if isinstance(configs, list) and configs:
                first = configs[0] if isinstance(configs[0], dict) else {}
                shape["first_model_keys"] = list(first.keys())[:20] if isinstance(first, dict) else []
                q = first.get("quotaInfo") or first.get("quota_info") if isinstance(first, dict) else None
                shape["first_quota"] = list(q.keys())[:15] if isinstance(q, dict) else type(q).__name__
    return shape


def _running_agy_pids() -> list[int]:
    try:
        proc = subprocess.run(["pgrep", "-x", "agy"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    return [int(x) for x in proc.stdout.split() if x.isdigit()]


def _first_quota_response(pid: int) -> Optional[dict[str, Any]]:
    last_ok: Optional[dict[str, Any]] = None
    for port in _listen_ports(pid):
        payload = _get_user_status(port)
        if payload is None:
            continue
        last_ok = payload
        if _has_quota(payload):
            return payload
    # Prefer a successful RPC body even without quotaInfo so callers can log shape
    # and optionally surface "answered but no windows".
    return last_ok


def _fetch_from_running() -> Optional[dict[str, Any]]:
    pids = _running_agy_pids()
    _log("debug", "reuse scan", pids=pids)
    for pid in pids:
        ports = _listen_ports(pid)
        _log("debug", "running agy", pid=pid, ports=ports)
        payload = _first_quota_response(pid)
        if payload is not None:
            _log("info", "reuse hit", pid=pid)
            return payload
    _log("debug", "reuse miss")
    return None


def _fetch_from_spawn(binary: str, timeout: float, settle: float) -> Optional[dict[str, Any]]:
    _log("info", "spawn start", binary=binary, timeout=timeout, settle=settle)
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
    _log("debug", "spawned", pid=proc.pid)
    try:
        deadline = time.time() + timeout
        payload = None
        attempts = 0
        while time.time() < deadline:
            _drain(master)  # the TUI keeps painting; a full pty buffer would stall it
            if proc.poll() is not None:
                _log("warning", "agy exited early", returncode=proc.returncode, attempts=attempts)
                return None
            attempts += 1
            ports = _listen_ports(proc.pid)
            if attempts == 1 or attempts % 10 == 0:
                _log("debug", "poll", attempt=attempts, ports=ports, elapsed=round(timeout - (deadline - time.time()), 1))
            payload = _first_quota_response(proc.pid)
            if payload is not None and _has_quota(payload):
                _log("info", "quota rpc ready", attempt=attempts, ports=ports)
                break
            if payload is not None and not _has_quota(payload):
                # Keep polling: server may answer before quotaInfo is populated.
                if attempts == 1 or attempts % 15 == 0:
                    _log("debug", "rpc body still missing quota", attempt=attempts, shape=_payload_shape(payload))
                payload = None
            time.sleep(POLL_INTERVAL)
        if payload is None:
            ports = _listen_ports(proc.pid)
            _log("warning", "spawn timeout", attempts=attempts, ports=ports, pid=proc.pid)
            return None
        if not _has_quota(payload):
            _log("warning", "rpc body without quota fields", shape=_payload_shape(payload), attempts=attempts)
            # Still return the body so check() can report "no quota windows" vs pure timeout.
            return payload
        # Values measured stable from the first successful read, but re-read
        # once after a short settle so a slow refresh cannot hand back defaults.
        if settle > 0:
            time.sleep(settle)
            _drain(master)
            refreshed = _first_quota_response(proc.pid)
            if refreshed is not None and _has_quota(refreshed):
                payload = refreshed
                _log("debug", "quota re-read after settle")
        return payload
    finally:
        _drain(master)
        _terminate(proc)
        _log("debug", "spawn terminated", pid=proc.pid)
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
    configs = _model_configs(payload)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for conf in configs:
        if not isinstance(conf, dict):
            continue
        quota = conf.get("quotaInfo") or conf.get("quota_info") or {}
        fraction = _quota_fraction(quota) if isinstance(quota, dict) else None
        if fraction is None:
            continue
        try:
            fraction = float(fraction)
            fraction = max(0.0, min(1.0, fraction))
        except (TypeError, ValueError):
            continue
        reset = str((quota or {}).get("resetTime") or (quota or {}).get("reset_time") or "")
        key = (f"{fraction:.6f}", reset)
        bucket = grouped.setdefault(
            key, {"fraction": fraction, "reset": reset, "models": [], "families": []}
        )
        label = str(conf.get("label") or conf.get("name") or "model")
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


def _build_result(payload: dict[str, Any], source: str, conf: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    conf = conf or {}
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

    warn_pct = _conf_float(conf, "warn_percent", "warn_pct", env="USAGE_MONITOR_AGY_WARN_PCT", default=DEFAULT_WARN_PCT)
    remaining_values = [p["fraction"] * 100 for p in pools]
    status = "ok"
    message = None
    if not windows:
        status = "unavailable"
        message = "agy answered but reported no quota windows"
    elif remaining_values and all(r <= 0 for r in remaining_values):
        status = "quota_exhausted"
        message = "Plan weekly quota exhausted (0% remaining)"
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


def check(conf: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    conf = conf if isinstance(conf, dict) else {}

    def unavailable(msg: str) -> dict[str, Any]:
        return {
            "id": PROVIDER_ID,
            "label": DEFAULT_LABEL,
            "status": "unavailable",
            "source": "agy_rpc",
            "message": msg,
        }

    cache_ttl = _conf_float(conf, "cache_ttl", env="USAGE_MONITOR_AGY_CACHE_TTL", default=DEFAULT_CACHE_TTL)
    cached = _read_cache(cache_ttl, conf)
    if cached is not None:
        _log("info", "cache hit", ttl=cache_ttl)
        result = _build_result(cached, "agy_rpc (cached)", conf)
        age = int(time.time() - _cache_path(conf).stat().st_mtime)
        result["details"].append(f"Cached {age}s ago")
        return result

    binary = _conf_str(conf, "bin", "agy_bin", env="USAGE_MONITOR_AGY_BIN") or shutil.which("agy") or ""
    reuse = _conf_bool(conf, "reuse", "reuse_running", env="USAGE_MONITOR_AGY_REUSE")
    _log("info", "check config", binary=binary or None, reuse=reuse, cache_ttl=cache_ttl)

    payload = None
    source = "agy_rpc"
    if reuse:
        payload = _fetch_from_running()
        if payload is not None:
            source = "agy_rpc (running instance)"

    if payload is None:
        if not binary:
            _log("error", "binary missing")
            return unavailable("`agy` binary not found (set bin: in providers.yaml or USAGE_MONITOR_AGY_BIN)")
        if not Path(binary).expanduser().exists():
            _log("error", "binary path missing", binary=binary)
            return unavailable(f"`agy` binary not found at {binary}")
        timeout = _conf_float(conf, "timeout", env="USAGE_MONITOR_AGY_TIMEOUT", default=DEFAULT_TIMEOUT)
        settle = _conf_float(conf, "settle", env="USAGE_MONITOR_AGY_SETTLE", default=DEFAULT_SETTLE)
        try:
            payload = _fetch_from_spawn(str(Path(binary).expanduser()), timeout, settle)
        except Exception as exc:
            _log("error", "spawn exception", error=str(exc)[:200])
            return unavailable(f"Failed to query agy: {str(exc)[:200]}")
        if payload is None:
            _log("error", "no quota after spawn", timeout=timeout)
            return unavailable(
                f"agy did not report quota data within {timeout:.0f}s "
                f"(RPC timeout — not the same as 0% plan quota; try timeout: 60 "
                f"or reuse: true with agy already open)"
            )

    pools = _pools(payload)
    _log("info", "quota parsed", pools=len(pools), source=source)
    _write_cache(payload, conf)
    return _build_result(payload, source, conf)


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))

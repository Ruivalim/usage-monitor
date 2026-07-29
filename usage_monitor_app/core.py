# SPDX-License-Identifier: MIT
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote as _url_quote

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

# Hermes is optional. HERMES_HOME is still resolved because Hermes-specific
# adapters (state.db) need it, but it no longer decides where this app stores
# its own state.
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def ensure_optional_hermes_agent_path() -> Path | None:
    """Make Hermes' optional Python package importable in LaunchAgent envs.

    Interactive shells usually export ``PYTHONPATH=$HOME/.hermes/hermes-agent``,
    but macOS LaunchAgents start with a tiny environment. Providers that use
    Hermes' credential pool/account-usage helpers should still work when the
    standalone app is launched at login, so add the known source root when it
    exists. Missing Hermes is fine: the app remains standalone and adapters
    will report unavailable credentials instead of crashing.
    """
    raw = os.environ.get("HERMES_PYTHON_SRC_ROOT") or str(HERMES_HOME / "hermes-agent")
    candidate = Path(raw).expanduser()
    if not (candidate / "agent").is_dir():
        return None
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)
    return candidate


ensure_optional_hermes_agent_path()


def default_app_home() -> Path:
    """Standalone default state/config dir: ``~/.config/usagemon``."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home).expanduser() / "usagemon"


def legacy_app_home() -> Path:
    """Pre-0.4 location of the app home (``$HERMES_HOME/usage``)."""
    return HERMES_HOME / "usage"


APP_HOME = Path(os.environ.get("USAGE_MONITOR_HOME") or default_app_home()).expanduser()
SNAPSHOT_DIR = APP_HOME
SNAPSHOT_FILE = Path(os.environ.get("USAGE_MONITOR_SNAPSHOT_FILE") or SNAPSHOT_DIR / "snapshots.jsonl").expanduser()
ADAPTER_DIR = Path(os.environ.get("USAGE_MONITOR_ADAPTER_DIR") or APP_HOME / "adapters").expanduser()
PROVIDERS_FILE = Path(os.environ.get("USAGE_MONITOR_PROVIDERS_FILE") or APP_HOME / "providers.yaml").expanduser()
PRICES_FILE = Path(os.environ.get("USAGE_MONITOR_PRICES_FILE") or APP_HOME / "prices.yaml").expanduser()
OVERRIDES_FILE = Path(os.environ.get("USAGE_MONITOR_OVERRIDES_FILE") or APP_HOME / "overrides.json").expanduser()
CONFIG_FILE = Path(os.environ.get("USAGE_MONITOR_CONFIG_FILE") or APP_HOME / "config.yaml").expanduser()

# Files worth carrying over from a pre-0.4 install. Adapters are copied as a
# directory tree; everything else is a single file.
MIGRATABLE_FILES = (
    "providers.yaml",
    "providers.json",
    "prices.yaml",
    "overrides.json",
    "snapshots.jsonl",
    "alert_state.json",
    "config.yaml",
)
MIGRATABLE_DIRS = ("adapters",)


def migrate_legacy_home(
    source: Path | None = None,
    destination: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Copy a pre-0.4 ``$HERMES_HOME/usage`` tree into the standalone home.

    Never overwrites an existing destination file and never deletes the
    source, so it is safe to run repeatedly (and to run while the old install
    is still in use).
    """
    src = Path(source).expanduser() if source else legacy_app_home()
    dst = Path(destination).expanduser() if destination else APP_HOME
    result: dict[str, list[str]] = {"copied": [], "skipped": [], "missing": []}
    if not src.is_dir():
        result["missing"].append(str(src))
        return result
    if src.resolve() == dst.resolve():
        result["skipped"].append(f"{src}: source and destination are the same directory")
        return result
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    for name in MIGRATABLE_FILES:
        src_file = src / name
        if not src_file.is_file():
            continue
        dst_file = dst / name
        if dst_file.exists():
            result["skipped"].append(str(dst_file))
            continue
        if not dry_run:
            shutil.copy2(src_file, dst_file)
        result["copied"].append(str(dst_file))
    for name in MIGRATABLE_DIRS:
        src_dir = src / name
        if not src_dir.is_dir():
            continue
        for src_file in sorted(src_dir.rglob("*")):
            if not src_file.is_file():
                continue
            dst_file = dst / name / src_file.relative_to(src_dir)
            if dst_file.exists():
                result["skipped"].append(str(dst_file))
                continue
            if not dry_run:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
            result["copied"].append(str(dst_file))
    return result


@dataclass
class Money:
    amount: float
    currency: str


@dataclass
class UsageWindow:
    label: str
    used_percent: Optional[float] = None
    remaining_percent: Optional[float] = None
    reset_at: Optional[str] = None
    detail: Optional[str] = None
    days_until_reset: Optional[int] = field(default=None, init=False)
    hours_until_reset: Optional[int] = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.reset_at:
            try:
                dt = datetime.fromisoformat(self.reset_at.replace("Z", "+00:00"))
                delta = dt - datetime.now(timezone.utc)
                total_seconds = max(0, delta.total_seconds())
                self.days_until_reset = int(total_seconds // 86400)
                self.hours_until_reset = int((total_seconds % 86400) // 3600)
            except (ValueError, TypeError):
                self.days_until_reset = None
                self.hours_until_reset = None

    def reset_label(self) -> Optional[str]:
        """Human-readable reset countdown: '3d', '2h', 'now', or None."""
        if self.days_until_reset is None:
            return None
        if self.days_until_reset > 0:
            return f"{self.days_until_reset}d"
        if self.hours_until_reset and self.hours_until_reset > 0:
            return f"{self.hours_until_reset}h"
        return "now"


@dataclass
class ProviderStatus:
    id: str
    label: str
    status: str = "unknown"  # ok | warning | error | rate_limited | quota_exhausted | unknown | unavailable
    source: str = "unknown"
    balance: Optional[Money] = None
    usage: Optional[Money] = None
    windows: list[UsageWindow] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    message: Optional[str] = None
    relevant: bool = True  # False excludes the provider from overall/alerts (still shown, muted)
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class MonitorSnapshot:
    checked_at: str
    overall: str
    providers: list[ProviderStatus]
    alerts: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_plain(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_plain(v) for k, v in asdict(obj).items() if v not in (None, [], {})}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items() if v not in (None, [], {})}
    return obj


def _config_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:
        raise RuntimeError(f"{path} requires PyYAML; install it or use JSON")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def load_provider_config(path: Path = PROVIDERS_FILE) -> dict[str, Any]:
    data = _config_load(path)
    if data.get("providers"):
        return data
    return {"providers": [], "defaults": {"timeout": 12.0}}


def load_prices(path: Path = PRICES_FILE) -> dict[str, Any]:
    try:
        return _config_load(path)
    except Exception:
        return {}


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Per-provider UI/aggregation overrides (e.g. {"nous": {"relevant": false}})."""
    path = Path(path) if path else OVERRIDES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def set_provider_relevant(provider_id: str, relevant: bool, path: Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else OVERRIDES_FILE
    overrides = load_overrides(path)
    entry = overrides.get(provider_id)
    if not isinstance(entry, dict):
        entry = {}
    entry["relevant"] = bool(relevant)
    if relevant:
        # True is the default; keep the file minimal.
        entry.pop("relevant")
        if entry:
            overrides[provider_id] = entry
        else:
            overrides.pop(provider_id, None)
    else:
        overrides[provider_id] = entry
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return overrides


def _http_get_json(url: str, token: str | None = None, *, timeout: float = 12.0, headers: Optional[dict[str, str]] = None) -> tuple[int, Any]:
    if httpx is None:
        raise RuntimeError("httpx is not available in this Python environment")
    final_headers = {"Accept": "application/json"}
    if token:
        final_headers["Authorization"] = f"Bearer {token}"
    if headers:
        final_headers.update(headers)
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=final_headers)
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:500]}
        return resp.status_code, body


def _status_from_http(code: int, body: Any, default: str = "ok") -> tuple[str, Optional[str]]:
    if 200 <= code < 300:
        return default, None
    msg = None
    if isinstance(body, dict):
        err = body.get("error") or body.get("message") or body.get("msg") or body.get("detail")
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("code") or err)[:240]
        elif err:
            msg = str(err)[:240]
    msg = msg or f"HTTP {code}"
    if code == 402:
        return "quota_exhausted", msg
    if code == 429:
        return "rate_limited", msg
    if code in (401, 403):
        return "error", msg
    return "warning", msg


def _keychain_password(*, service: str, account: str | None = None) -> str | None:
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[2:2] = ["-a", account]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=8)
        if proc.returncode == 0:
            token = proc.stdout.strip()
            return token or None
    except Exception:
        return None
    return None


def _pool_entry(provider: str):
    try:
        from agent.credential_pool import load_pool  # type: ignore
        pool = load_pool(provider)
        return pool.select()
    except Exception:
        return None


def _hermes_runtime_token(provider: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    entry = _pool_entry(provider)
    if entry is None:
        return None, None, None
    token = str(getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", None) or "").strip()
    base_url = str(getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or "").strip().rstrip("/")
    source = str(getattr(entry, "source", "credential_pool") or "credential_pool")
    return token or None, base_url or None, source


def _credential(conf: dict[str, Any]) -> tuple[Optional[str], Optional[str], str]:
    cred = conf.get("credential") or {}
    if not isinstance(cred, dict):
        return None, None, "config"
    source = str(cred.get("source") or "env")
    if source == "env":
        return os.environ.get(str(cred.get("name") or "")), conf.get("base_url"), "env"
    if source == "keychain":
        token = _keychain_password(service=str(cred.get("service") or conf.get("id")), account=cred.get("account"))
        return token, conf.get("base_url"), "keychain"
    if source == "literal":
        return str(cred.get("value") or "") or None, conf.get("base_url"), "config"
    if source == "hermes":
        token, base_url, hermes_source = _hermes_runtime_token(str(cred.get("provider") or conf.get("id")))
        return token, conf.get("base_url") or base_url, hermes_source or "hermes"
    return None, conf.get("base_url"), source


def _money_from_mapping(data: dict[str, Any], keys: tuple[str, ...], currency: str) -> Money | None:
    for key in keys:
        amount = _safe_float(data.get(key))
        if amount is not None:
            return Money(amount, currency)
    return None


_ACCOUNT_USAGE_LABELS = {"Session": "Current session", "Weekly": "Current week"}


def _from_account_usage(provider: str, label: str) -> ProviderStatus:
    try:
        from agent.account_usage import fetch_account_usage  # type: ignore
        snap = fetch_account_usage(provider)
        if snap is None:
            return ProviderStatus(provider, label, status="unavailable", source="hermes_account_usage", message="No account usage API/credentials available")
        status = "ok" if not getattr(snap, "unavailable_reason", None) else "unavailable"
        windows: list[UsageWindow] = []
        for w in getattr(snap, "windows", ()) or ():
            used = _safe_float(getattr(w, "used_percent", None))
            reset_dt = getattr(w, "reset_at", None)
            raw_label = str(getattr(w, "label", "window"))
            window_label = _ACCOUNT_USAGE_LABELS.get(raw_label, raw_label)
            if raw_label == "Session" and reset_dt is not None:
                # Some accounts report a 7-day "primary_window" as Session;
                # relabel by reset distance so it reads like reality.
                try:
                    if (reset_dt - datetime.now(timezone.utc)).total_seconds() > 24 * 3600:
                        window_label = "Current week"
                except Exception:
                    pass
            windows.append(UsageWindow(
                label=window_label,
                used_percent=used,
                remaining_percent=(max(0.0, 100.0 - used) if used is not None else None),
                reset_at=(reset_dt.isoformat() if reset_dt is not None else None),
                detail=getattr(w, "detail", None),
            ))
        details = [str(x) for x in (getattr(snap, "details", ()) or ())]
        msg = getattr(snap, "unavailable_reason", None)
        if any((w.used_percent or 0) >= 100 for w in windows):
            status = "quota_exhausted"
        elif any((w.used_percent or 0) >= 85 for w in windows) and status == "ok":
            status = "warning"
        return ProviderStatus(provider, label, status=status, source=str(getattr(snap, "source", "hermes_account_usage")), windows=windows, details=details, message=msg)
    except Exception as exc:
        return ProviderStatus(provider, label, status="unavailable", source="hermes_account_usage", message=str(exc)[:240])


def _adapter_hermes_auth(_conf: dict[str, Any]) -> list[ProviderStatus]:
    try:
        proc = subprocess.run(["hermes", "auth", "list"], text=True, capture_output=True, timeout=20)
        text = (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return [ProviderStatus("hermes-auth", "Hermes auth", status="unavailable", source="cli", message=str(exc)[:240])]
    providers: list[ProviderStatus] = []
    current: Optional[ProviderStatus] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and "(" in line:
            pid = line.split("(", 1)[0].strip()
            current = ProviderStatus(id=f"auth:{pid}", label=f"Auth: {pid}", status="ok", source="hermes auth list")
            providers.append(current)
            continue
        if current is not None:
            lower = line.lower()
            if "rate-limited" in lower:
                current.status = "rate_limited"
                current.message = line.strip()
            elif "exceeded" in lower or "quota" in lower:
                current.status = "quota_exhausted"
                current.message = line.strip()
            current.details.append(line.strip())
    if not providers:
        return [ProviderStatus("hermes-auth", "Hermes auth", status="unknown", source="cli", message="No Hermes auth providers listed")]
    return providers


def _adapter_deepseek(conf: dict[str, Any]) -> ProviderStatus:
    token, _base_url, source = _credential(conf)
    if not token:
        return ProviderStatus(conf["id"], conf.get("label", conf["id"]), status="unavailable", source=source, message="No DeepSeek credential")
    try:
        code, body = _http_get_json("https://api.deepseek.com/user/balance", token, timeout=float(conf.get("timeout", 12.0)))
        status, msg = _status_from_http(code, body)
        ps = ProviderStatus(conf["id"], conf.get("label", conf["id"]), status=status, source=source, message=msg)
        if 200 <= code < 300 and isinstance(body, dict):
            if body.get("is_available") is False:
                ps.status = "quota_exhausted"
            for info in body.get("balance_infos") or []:
                if isinstance(info, dict):
                    currency = str(info.get("currency") or "CNY").upper()
                    total = _safe_float(info.get("total_balance"))
                    if total is not None and ps.balance is None:
                        ps.balance = Money(total, currency)
                    bits = []
                    for k, name in (("granted_balance", "granted"), ("topped_up_balance", "topped up")):
                        v = _safe_float(info.get(k))
                        if v is not None:
                            bits.append(f"{name}: {v:.2f} {currency}")
                    if bits:
                        ps.details.append("; ".join(bits))
        return ps
    except Exception as exc:
        return ProviderStatus(conf["id"], conf.get("label", conf["id"]), status="unavailable", source=source, message=str(exc)[:240])


def _adapter_kimi(conf: dict[str, Any]) -> ProviderStatus:
    token, base_url, source = _credential(conf)
    if not token:
        return ProviderStatus(conf["id"], conf.get("label", conf["id"]), status="unavailable", source=source, message="No Kimi credential")
    base = (base_url or "https://api.moonshot.ai/v1").rstrip("/")
    try:
        code, body = _http_get_json(f"{base}/users/me/balance", token, timeout=float(conf.get("timeout", 12.0)))
        status, msg = _status_from_http(code, body)
        ps = ProviderStatus(conf["id"], conf.get("label", conf["id"]), status=status, source=source, message=msg)
        if 200 <= code < 300 and isinstance(body, dict):
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            currency = str(data.get("currency") or "CNY").upper() if isinstance(data, dict) else "CNY"
            if isinstance(data, dict):
                ps.balance = _money_from_mapping(data, ("available_balance", "balance", "total_balance", "available"), currency)
                for k, name in (("voucher_balance", "voucher"), ("cash_balance", "cash"), ("used_balance", "used")):
                    v = _safe_float(data.get(k))
                    if v is not None:
                        ps.details.append(f"{name}: {v:.2f}")
                if ps.balance is None:
                    ps.details.append("Balance endpoint returned unrecognized shape")
        return ps
    except Exception as exc:
        return ProviderStatus(conf["id"], conf.get("label", conf["id"]), status="unavailable", source=source, message=str(exc)[:240])


def _adapter_openai_compatible(conf: dict[str, Any]) -> ProviderStatus:
    token, base_url, source = _credential(conf)
    label = conf.get("label", conf["id"])
    if not base_url:
        return ProviderStatus(conf["id"], label, status="unavailable", source=source, message="No base_url configured")
    if not token:
        return ProviderStatus(conf["id"], label, status="unavailable", source=source, message="No API credential")
    endpoint = str(conf.get("credits_endpoint") or conf.get("models_endpoint") or "/models")
    url = endpoint if endpoint.startswith("http") else f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        code, body = _http_get_json(url, token, timeout=float(conf.get("timeout", 12.0)), headers=conf.get("headers") if isinstance(conf.get("headers"), dict) else None)
        status, msg = _status_from_http(code, body)
        ps = ProviderStatus(conf["id"], label, status=status, source=source, message=msg)
        if 200 <= code < 300 and isinstance(body, dict):
            currency = str(conf.get("currency") or body.get("currency") or "USD").upper()
            ps.balance = _money_from_mapping(body.get("data", body) if isinstance(body.get("data", body), dict) else body, ("balance", "credits", "credit", "available", "remaining"), currency)
            if ps.balance is None:
                count = len(body.get("data", [])) if isinstance(body.get("data"), list) else None
                ps.details.append(f"Endpoint reachable" + (f"; models: {count}" if count is not None else ""))
        return ps
    except Exception as exc:
        return ProviderStatus(conf["id"], label, status="unavailable", source=source, message=str(exc)[:240])


def _adapter_placeholder(conf: dict[str, Any]) -> ProviderStatus:
    token, base_url, source = _credential(conf)
    return ProviderStatus(conf["id"], conf.get("label", conf["id"]), status="unknown" if token else "unavailable", source=source, message=conf.get("message") or "No balance endpoint configured", details=[f"base_url: {base_url}" if base_url else "No runtime base_url"])


def _adapter_hermes_nous(conf: dict[str, Any]) -> ProviderStatus:
    try:
        from agent.account_usage import nous_credits_lines  # type: ignore
        lines = nous_credits_lines(timeout=10.0)
        if not lines:
            return ProviderStatus(conf["id"], conf.get("label", "Nous Portal"), status="unavailable", source="portal", message="No Nous portal credit data available")
        ps = ProviderStatus(conf["id"], conf.get("label", "Nous Portal"), status="ok", source="portal", details=lines)
        for line in lines:
            if "access depleted" in line.lower():
                ps.status = "quota_exhausted"
        return ps
    except Exception as exc:
        return ProviderStatus(conf["id"], conf.get("label", "Nous Portal"), status="unavailable", source="portal", message=str(exc)[:240])


# Hermes state.db read-only adapter. Observed schema (2026-07): table
# session_model_usage(session_id, model, billing_provider, billing_base_url,
# billing_mode, task, api_call_count, input_tokens, output_tokens,
# cache_read_tokens, cache_write_tokens, reasoning_tokens,
# estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
# first_seen, last_seen) plus a sessions table with profile_name/started_at.
_STATE_USAGE_TABLE = "session_model_usage"
_STATE_USAGE_COLUMNS = (
    "session_id",
    "model",
    "billing_provider",
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
    "last_seen",
)


def _state_db_paths(conf: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    raw = conf.get("db_paths")
    if isinstance(raw, (list, tuple)):
        paths.extend(Path(str(p)).expanduser() for p in raw if str(p).strip())
    elif isinstance(raw, str) and raw.strip():
        paths.append(Path(raw).expanduser())
    single = conf.get("path")
    if isinstance(single, str) and single.strip():
        paths.append(Path(single).expanduser())
    if not paths:
        paths.append(HERMES_HOME / "state.db")
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _parse_state_ts(value: Any) -> Optional[float]:
    """Best-effort parse of a state.db timestamp into epoch seconds.

    Handles ISO-8601 strings and numeric epoch seconds/milliseconds.
    Returns None when the value is missing or unparseable.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    if not text:
        return None
    try:
        v = float(text)
        return v / 1000.0 if v > 1e12 else v
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _adapter_hermes_state_db(conf: dict[str, Any]) -> list[ProviderStatus]:
    base_id = str(conf.get("id") or "hermes-state-db")
    label = str(conf.get("label") or base_id)
    source = "hermes-state-db"

    def _one(status: str, message: str) -> list[ProviderStatus]:
        return [ProviderStatus(base_id, label, status=status, source=source, message=message[:240])]

    paths = _state_db_paths(conf)
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return _one("unavailable", "state.db not found: " + ", ".join(str(p) for p in paths))
    notes = [f"skipped missing db: {p}" for p in paths if not p.is_file()]

    profile = str(conf.get("profile") or "").strip()
    limit_days = _safe_float(conf.get("limit_days"))
    cutoff = time.time() - limit_days * 86400.0 if limit_days and limit_days > 0 else None

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    select_cols = ", ".join(_STATE_USAGE_COLUMNS)
    for path in existing:
        try:
            # Read-only URI: never writes, never takes a write lock.
            conn = sqlite3.connect(f"file:{_url_quote(str(path))}?mode=ro", uri=True, timeout=5)
        except Exception as exc:
            return _one("unavailable", f"cannot open {path} read-only: {exc}")
        try:
            tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if _STATE_USAGE_TABLE not in tables:
                return _one("unknown", f"schema drift in {path}: missing table {_STATE_USAGE_TABLE}")
            cols = _sqlite_columns(conn, _STATE_USAGE_TABLE)
            missing_cols = [c for c in _STATE_USAGE_COLUMNS if c not in cols]
            if missing_cols:
                return _one("unknown", f"schema drift in {path}: missing columns {', '.join(missing_cols)}")
            session_filter: Optional[set[str]] = None
            if profile:
                sess_cols = _sqlite_columns(conn, "sessions") if "sessions" in tables else set()
                join_key = "session_id" if "session_id" in sess_cols else "id" if "id" in sess_cols else None
                if "profile_name" not in sess_cols or join_key is None:
                    return _one("unknown", f"schema drift in {path}: sessions table lacks profile_name/join key for profile filter")
                session_filter = {str(r[0]) for r in conn.execute(f"SELECT {join_key} FROM sessions WHERE profile_name = ?", (profile,))}
            for row in conn.execute(f"SELECT {select_cols} FROM {_STATE_USAGE_TABLE}"):
                rec = dict(zip(_STATE_USAGE_COLUMNS, row))
                if session_filter is not None and str(rec["session_id"]) not in session_filter:
                    continue
                if cutoff is not None:
                    ts = _parse_state_ts(rec["last_seen"])
                    if ts is not None and ts < cutoff:
                        continue
                provider = str(rec["billing_provider"] or "unknown")
                model = str(rec["model"] or "unknown")
                g = groups.setdefault((provider, model), {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0, "est": 0.0, "actual": 0.0, "has_actual": False})
                g["calls"] += int(_safe_float(rec["api_call_count"]) or 0)
                g["input"] += int(_safe_float(rec["input_tokens"]) or 0)
                g["output"] += int(_safe_float(rec["output_tokens"]) or 0)
                g["cache_read"] += int(_safe_float(rec["cache_read_tokens"]) or 0)
                g["cache_write"] += int(_safe_float(rec["cache_write_tokens"]) or 0)
                g["reasoning"] += int(_safe_float(rec["reasoning_tokens"]) or 0)
                est_cost = _safe_float(rec["estimated_cost_usd"])
                actual_cost = _safe_float(rec["actual_cost_usd"])
                g["est"] += est_cost or 0.0
                if actual_cost is not None:
                    g["actual"] += actual_cost
                    g["has_actual"] = True
        except Exception as exc:
            return _one("unavailable", f"failed reading {path}: {exc}")
        finally:
            conn.close()

    if not groups:
        return [ProviderStatus(base_id, label, status="ok", source=source, message="No usage rows matched filters", details=notes)]

    # Optional price-table enrichment: estimate costs only for provider/model
    # groups where state.db recorded neither actual nor estimated USD cost.
    # Estimates stay in details, keyed by their own currency, and are never
    # summed into the USD `usage` figure.
    price_table = None
    try:
        from .costs import estimate_cost, load_price_table, sum_estimates

        price_table = load_price_table(PRICES_FILE)
    except Exception:
        estimate_cost = sum_estimates = None  # type: ignore[assignment]

    by_provider: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for (provider, model), g in groups.items():
        by_provider.setdefault(provider, []).append((model, g))

    out: list[ProviderStatus] = []
    for provider in sorted(by_provider):
        items = sorted(by_provider[provider])
        total_calls = sum(g["calls"] for _, g in items)
        total_est = sum(g["est"] for _, g in items)
        total_actual = sum(g["actual"] for _, g in items)
        has_actual = any(g["has_actual"] for _, g in items)
        db_cost = total_actual if has_actual else total_est
        details = list(notes)
        estimates = []
        for model, g in items:
            tokens = g["input"] + g["output"] + g["cache_read"] + g["cache_write"] + g["reasoning"]
            details.append(f"{model}: {g['calls']} calls, {tokens} tokens, est ${g['est']:.4f}, actual ${g['actual']:.4f}")
            if price_table is not None and not g["has_actual"] and g["est"] <= 0:
                estimate = estimate_cost(price_table, provider, model, input_tokens=g["input"], output_tokens=g["output"], cached_tokens=g["cache_read"])
                if estimate is not None:
                    estimates.append(estimate)
        if estimates:
            for currency, amount in sorted(sum_estimates(estimates).items()):
                details.append(f"price-table estimate: {amount:.4f} {currency}")
        has_cost = has_actual or total_est > 0
        out.append(ProviderStatus(
            id=f"{base_id}:{provider}",
            label=f"{label}: {provider}",
            status="ok",
            source=source,
            usage=Money(round(db_cost, 6), "USD") if has_cost else None,
            details=details,
            message=f"{total_calls} calls, ${db_cost:.2f} ({'actual' if has_actual else 'estimated'})" if has_cost else f"{total_calls} calls, no cost data",
        ))
    return out


REGISTRY: dict[str, Callable[[dict[str, Any]], ProviderStatus | list[ProviderStatus]]] = {
    "deepseek": _adapter_deepseek,
    "kimi": _adapter_kimi,
    "openai-compatible": _adapter_openai_compatible,
    "generic-http": _adapter_openai_compatible,
    "placeholder": _adapter_placeholder,
    "hermes-account-usage": lambda conf: _from_account_usage(str(conf.get("provider") or conf["id"]), str(conf.get("label") or conf["id"])),
    "anthropic-subscription": lambda conf: _from_account_usage(str(conf.get("provider") or "anthropic"), str(conf.get("label") or "Claude / Anthropic")),
    "hermes-auth": _adapter_hermes_auth,
    "hermes-nous": _adapter_hermes_nous,
    "hermes-state-db": _adapter_hermes_state_db,
}


def _load_external_adapters() -> list[Callable[[], ProviderStatus | dict[str, Any] | list[Any]]]:
    adapters = []
    if not ADAPTER_DIR.exists():
        return adapters
    for path in sorted(ADAPTER_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"usage_adapter_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            check = getattr(mod, "check", None)
            if callable(check):
                adapters.append(check)
        except Exception:
            continue
    return adapters


def _coerce_window(value: Any) -> UsageWindow:
    if isinstance(value, UsageWindow):
        return value
    if isinstance(value, dict):
        return UsageWindow(
            label=str(value.get("label") or value.get("name") or "window"),
            used_percent=_safe_float(value.get("used_percent")),
            remaining_percent=_safe_float(value.get("remaining_percent")),
            reset_at=str(value.get("reset_at")) if value.get("reset_at") else None,
            detail=str(value.get("detail")) if value.get("detail") else None,
        )
    raise TypeError(f"Unsupported window return: {type(value).__name__}")


def _coerce_provider(value: Any) -> ProviderStatus:
    if isinstance(value, ProviderStatus):
        return value
    if isinstance(value, dict):
        bal = value.get("balance")
        balance = None
        if isinstance(bal, dict) and bal.get("amount") is not None:
            balance = Money(float(bal["amount"]), str(bal.get("currency") or "USD"))
        usage_value = value.get("usage")
        usage = None
        if isinstance(usage_value, dict) and usage_value.get("amount") is not None:
            usage = Money(float(usage_value["amount"]), str(usage_value.get("currency") or "USD"))
        windows_raw = value.get("windows", []) if isinstance(value.get("windows", []), list) else []
        return ProviderStatus(
            id=str(value.get("id") or value.get("name") or "external"),
            label=str(value.get("label") or value.get("id") or "External"),
            status=str(value.get("status") or "unknown"),
            source=str(value.get("source") or "external"),
            balance=balance,
            usage=usage,
            windows=[_coerce_window(w) for w in windows_raw],
            details=[str(x) for x in value.get("details", [])] if isinstance(value.get("details", []), list) else [],
            message=str(value.get("message")) if value.get("message") else None,
        )
    raise TypeError(f"Unsupported adapter return: {type(value).__name__}")


def _provider_items_from_config(config: dict[str, Any], *, include_auth: bool) -> list[dict[str, Any]]:
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        return []
    defaults = config.get("defaults", {}) if isinstance(config.get("defaults"), dict) else {}
    out = []
    for item in providers:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        merged = {**defaults, **item}
        if not include_auth and str(merged.get("type")) == "hermes-auth":
            continue
        out.append(merged)
    return out


def collect_status(*, include_auth: bool = True, persist: bool = True, providers_file: Path = PROVIDERS_FILE) -> MonitorSnapshot:
    providers: list[ProviderStatus] = []
    errors: list[str] = []
    try:
        config = load_provider_config(providers_file)
    except Exception as exc:
        config = {"providers": []}
        errors.append(f"config:{providers_file}: {exc}")
    for conf in _provider_items_from_config(config, include_auth=include_auth):
        adapter_type = str(conf.get("type") or "placeholder")
        adapter = REGISTRY.get(adapter_type)
        if adapter is None:
            providers.append(ProviderStatus(str(conf.get("id") or adapter_type), str(conf.get("label") or conf.get("id") or adapter_type), status="unknown", source="config", message=f"Unknown provider type: {adapter_type}"))
            continue
        try:
            result = adapter(conf)
            items = result if isinstance(result, list) else [result]
            for item in items:
                providers.append(_coerce_provider(item))
        except Exception as exc:
            errors.append(f"{conf.get('id', adapter_type)}: {exc}")
            providers.append(ProviderStatus(id=str(conf.get("id") or adapter_type), label=str(conf.get("label") or "Adapter error"), status="error", source="adapter", message=str(exc)[:240], details=[traceback.format_exc(limit=2)]))
    for adapter in _load_external_adapters():
        try:
            result = adapter()
            items = result if isinstance(result, list) else [result]
            for item in items:
                providers.append(_coerce_provider(item))
        except Exception as exc:
            errors.append(f"{getattr(adapter, '__name__', 'adapter')}: {exc}")
            providers.append(ProviderStatus(id=f"adapter-error:{getattr(adapter, '__name__', 'adapter')}", label="Adapter error", status="error", source="adapter", message=str(exc)[:240], details=[traceback.format_exc(limit=2)]))
    overrides = load_overrides()
    for p in providers:
        entry = overrides.get(p.id)
        if isinstance(entry, dict) and entry.get("relevant") is False:
            p.relevant = False
    alerts: list[dict[str, Any]] = []
    severity_order = {"ok": 0, "unknown": 1, "unavailable": 1, "warning": 2, "rate_limited": 2, "quota_exhausted": 3, "error": 3}
    worst = 0
    for p in providers:
        if not p.relevant:
            continue  # muted by the user: shown in UIs but ignored for overall/alerts
        sev = severity_order.get(p.status, 1)
        worst = max(worst, sev)
        if sev >= 2:
            alerts.append({"level": "error" if sev >= 3 else "warning", "provider": p.id, "message": p.message or p.status})
    overall = "ok" if worst == 0 else "unknown" if worst == 1 else "warning" if worst == 2 else "error"
    snap = MonitorSnapshot(_now(), overall, providers, alerts, meta={"provider_config": str(providers_file), "external_adapter_dir": str(ADAPTER_DIR), "prices_file": str(PRICES_FILE), "errors": errors})
    if persist:
        SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_to_plain(snap), ensure_ascii=False, sort_keys=True) + "\n")
    return snap


def snapshot_json(*, pretty: bool = False, include_auth: bool = True, persist: bool = True) -> str:
    data = _to_plain(collect_status(include_auth=include_auth, persist=persist))
    return json.dumps(data, indent=2 if pretty else None, ensure_ascii=False, sort_keys=True)


def latest_snapshot(limit: int = 1) -> list[dict[str, Any]]:
    if not SNAPSHOT_FILE.exists():
        return []
    lines = SNAPSHOT_FILE.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def render_text(snapshot: Optional[MonitorSnapshot] = None) -> str:
    snap = snapshot or collect_status()
    lines = [f"API Usage Monitor — {snap.overall} — {snap.checked_at}"]
    for p in snap.providers:
        bal = f" {p.balance.amount:.2f} {p.balance.currency}" if p.balance else ""
        windows = ""
        if p.windows:
            parts = []
            for w in p.windows:
                if w.remaining_percent is not None:
                    entry = f"{w.label}: {w.remaining_percent:.0f}% left"
                    rl = w.reset_label()
                    if rl:
                        entry += f" · resets in {rl}"
                    parts.append(entry)
            windows = " | " + "; ".join(parts) if parts else ""
        msg = f" — {p.message}" if p.message else ""
        lines.append(f"- {p.label}: {p.status}{bal}{windows}{msg}")
    return "\n".join(lines)

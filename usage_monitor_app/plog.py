# SPDX-License-Identifier: MIT
"""Provider-aware logging for usage-monitor.

Controlled by ``config.yaml``::

    logging:
      enabled: true
      level: debug          # debug | info | warning | error
      path: ~/.config/usagemon/logs/usagemon.log
      only:                 # empty = all providers when enabled
        - antigravity
        - codex

Per-entry override in ``providers.yaml``::

    - id: antigravity
      type: antigravity
      log: true             # force logging for this entry
      # log: false          # silence even if in only/global
      log_level: debug      # optional per-entry floor

Env: ``USAGE_MONITOR_LOG=1``, ``USAGE_MONITOR_LOG_LEVEL=debug``,
``USAGE_MONITOR_LOG_PATH=...``, ``USAGE_MONITOR_LOG_ONLY=antigravity,codex``.

Never log secrets (tokens, passwords, keychain values).
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_lock = threading.Lock()
_current: ContextVar[Optional["LogContext"]] = ContextVar("usagemon_log_ctx", default=None)
_settings: Optional["LogSettings"] = None


@dataclass
class LogSettings:
    enabled: bool = False
    level: str = "info"
    path: Path = field(default_factory=lambda: _default_log_path())
    only: set[str] = field(default_factory=set)  # provider id or type; empty = all


@dataclass
class LogContext:
    provider_id: str
    provider_type: str
    enabled: bool
    level: int


def _default_log_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home).expanduser() / "usagemon" / "logs" / "usagemon.log"


def _parse_level(raw: Any, default: str = "info") -> str:
    text = str(raw or default).strip().lower()
    return text if text in _LEVELS else default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_log_settings(
    app_config: Any | None = None,
    *,
    home: Path | None = None,
) -> LogSettings:
    """Load settings from config.yaml / env. Safe if config missing."""
    enabled = False
    level = "info"
    path = _default_log_path() if home is None else Path(home) / "logs" / "usagemon.log"
    only: set[str] = set()

    raw: dict[str, Any] = {}
    if app_config is not None:
        # AppConfig may expose .logging or public_dict; prefer attribute.
        log_obj = getattr(app_config, "logging", None)
        if log_obj is not None and hasattr(log_obj, "enabled"):
            enabled = bool(log_obj.enabled)
            level = _parse_level(getattr(log_obj, "level", level), level)
            p = getattr(log_obj, "path", None)
            if p:
                path = Path(str(p)).expanduser()
            only = set(getattr(log_obj, "only", None) or [])
        elif isinstance(getattr(app_config, "raw_logging", None), dict):
            raw = app_config.raw_logging
    if not raw and app_config is None:
        try:
            from .config import load_app_config

            cfg = load_app_config()
            return load_log_settings(cfg, home=home)
        except Exception:
            pass

    if raw:
        enabled = _as_bool(raw.get("enabled"), False)
        level = _parse_level(raw.get("level"), "info")
        if raw.get("path"):
            path = Path(str(raw["path"])).expanduser()
        only_raw = raw.get("only") or raw.get("providers") or []
        if isinstance(only_raw, str):
            only = {x.strip() for x in only_raw.split(",") if x.strip()}
        elif isinstance(only_raw, (list, tuple, set)):
            only = {str(x).strip() for x in only_raw if str(x).strip()}
        elif isinstance(only_raw, dict):
            only = {str(k).strip() for k, v in only_raw.items() if _as_bool(v, False)}

    # Env overrides
    if os.environ.get("USAGE_MONITOR_LOG", "").strip() in ("1", "true", "yes", "on"):
        enabled = True
    if os.environ.get("USAGE_MONITOR_LOG", "").strip() in ("0", "false", "no", "off"):
        enabled = False
    if os.environ.get("USAGE_MONITOR_LOG_LEVEL"):
        level = _parse_level(os.environ.get("USAGE_MONITOR_LOG_LEVEL"), level)
    if os.environ.get("USAGE_MONITOR_LOG_PATH"):
        path = Path(os.environ["USAGE_MONITOR_LOG_PATH"]).expanduser()
    if os.environ.get("USAGE_MONITOR_LOG_ONLY"):
        only = {x.strip() for x in os.environ["USAGE_MONITOR_LOG_ONLY"].split(",") if x.strip()}

    return LogSettings(enabled=enabled, level=level, path=path, only=only)


def configure(settings: LogSettings | None = None) -> LogSettings:
    global _settings
    _settings = settings or load_log_settings()
    return _settings


def get_settings() -> LogSettings:
    global _settings
    if _settings is None:
        _settings = load_log_settings()
    return _settings


def _entry_allows(conf: dict[str, Any], settings: LogSettings) -> tuple[bool, int]:
    """Return (enabled, min_level) for this provider conf."""
    pid = str(conf.get("id") or "")
    ptype = str(conf.get("type") or "")
    level_name = _parse_level(conf.get("log_level") or settings.level, settings.level)
    min_level = _LEVELS[level_name]

    if "log" in conf:
        return _as_bool(conf.get("log"), False), min_level

    if not settings.enabled:
        return False, min_level

    if not settings.only:
        return True, min_level

    if pid in settings.only or ptype in settings.only or "*" in settings.only:
        return True, min_level
    return False, min_level


@contextmanager
def provider_scope(conf: dict[str, Any]) -> Iterator[Optional[LogContext]]:
    settings = get_settings()
    enabled, min_level = _entry_allows(conf, settings)
    ctx = LogContext(
        provider_id=str(conf.get("id") or "?"),
        provider_type=str(conf.get("type") or "?"),
        enabled=enabled,
        level=min_level,
    )
    token = _current.set(ctx)
    try:
        if enabled:
            log("info", "check start", type=ctx.provider_type)
        yield ctx
    finally:
        _current.reset(token)


def _should_emit(level: str) -> bool:
    ctx = _current.get()
    if ctx is None or not ctx.enabled:
        return False
    return _LEVELS.get(level, 20) >= ctx.level


def _safe_extra(extra: dict[str, Any]) -> dict[str, Any]:
    redact_keys = {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "authorization",
        "api_key",
        "secret",
        "credential",
        "key",
    }
    out: dict[str, Any] = {}
    for k, v in extra.items():
        lk = str(k).lower()
        if any(r in lk for r in redact_keys):
            out[k] = "<redacted>"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, Path):
            out[k] = str(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) if not isinstance(x, (str, int, float, bool, type(None))) else x for x in v[:50]]
        elif isinstance(v, dict):
            out[k] = _safe_extra(v)
        else:
            out[k] = repr(v)[:200]
    return out


def log(level: str, message: str, **extra: Any) -> None:
    level = _parse_level(level, "info")
    if not _should_emit(level):
        return
    ctx = _current.get()
    settings = get_settings()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "provider": ctx.provider_id if ctx else None,
        "type": ctx.provider_type if ctx else None,
        "msg": message,
    }
    if extra:
        record["extra"] = _safe_extra(extra)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        try:
            settings.path.parent.mkdir(parents=True, exist_ok=True)
            with settings.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def debug(message: str, **extra: Any) -> None:
    log("debug", message, **extra)


def info(message: str, **extra: Any) -> None:
    log("info", message, **extra)


def warning(message: str, **extra: Any) -> None:
    log("warning", message, **extra)


def error(message: str, **extra: Any) -> None:
    log("error", message, **extra)


def exception(message: str, exc: BaseException | None = None, **extra: Any) -> None:
    if exc is not None:
        extra = {**extra, "error": str(exc)[:400], "traceback": traceback.format_exc(limit=4)}
    log("error", message, **extra)

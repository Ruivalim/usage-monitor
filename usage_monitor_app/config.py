# SPDX-License-Identifier: MIT
"""App settings (``config.yaml``): dashboard defaults and dashboard/API auth.

Separate from ``providers.yaml`` on purpose: that file describes *what* to
monitor, this one describes *how the local app behaves*. Missing file means
"defaults", never an error — a fresh install has no ``config.yaml`` at all.

Auth is opt-in and local-only by default. Secrets are read from the
environment first (``USAGE_MONITOR_AUTH_*``), so a config file committed by
mistake never carries a usable credential. When a password must live in the
file, prefer ``password_hash`` (PBKDF2-SHA256, produced by
``usagemon auth hash``); plaintext ``password`` is accepted for local
development only.

Shape::

    dashboard:

      theme: light            # light | dark
      language: en            # en | pt

    intervals:
      refresh_seconds: 900    # backend scheduler interval
      menubar_seconds: 300    # tray auto-refresh interval

    auth:
      enabled: false
      realm: usage-monitor
      username: local
      password_hash: pbkdf2_sha256$390000$<salt-hex>$<hash-hex>
      # password: plain-text-for-local-dev-only
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import APP_HOME, CONFIG_FILE, _config_load

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390_000
DEFAULT_REALM = "usage-monitor"
DEFAULT_USERNAME = "local"
DEFAULT_THEME = "light"
DEFAULT_LANGUAGE = "en"
DEFAULT_REFRESH_SECONDS = 900
DEFAULT_MENUBAR_SECONDS = 300

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

LOCAL_TOKEN_HEADER = "x-usage-monitor-token"


def local_token_file() -> Path:
    return Path(os.environ.get("USAGE_MONITOR_LOCAL_TOKEN_FILE") or APP_HOME / "local-token").expanduser()


def read_local_token(path: Path | None = None) -> str | None:
    """Read the loopback refresh token, or None when it has not been issued."""
    target = Path(path).expanduser() if path else local_token_file()
    try:
        return target.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def ensure_local_token(path: Path | None = None) -> str:
    """Return the loopback refresh token, minting it on first use.

    Same-user processes (the menu bar app) need a way to ask the backend for a
    refresh once Basic Auth is on, and the stored password is a one-way hash
    they cannot replay. This grants exactly that one call — see the middleware
    in ``web.py``, which accepts the token only on the refresh routes — and
    lives in a 0600 file next to ``config.yaml``, so reading it already implies
    the ability to read the config and restart the server.
    """
    target = Path(path).expanduser() if path else local_token_file()
    existing = read_local_token(target)
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start: never widen, never race a reader.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    os.chmod(target, 0o600)
    return token


def verify_local_token(candidate: str | None, expected: str | None) -> bool:
    """Constant-time comparison; false whenever either side is missing."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --- password hashing --------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    """Return a ``pbkdf2_sha256$iterations$salt$hash`` string."""
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${salt_bytes.hex()}${digest.hex()}"


def verify_password_hash(stored: str, password: str) -> bool:
    """Constant-time check of ``password`` against a stored PBKDF2 string."""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != PBKDF2_ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        rounds = int(iterations)
    except (ValueError, binascii.Error):
        return False
    if rounds <= 0 or not salt or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(candidate, expected)


def generate_password(length: int = 24) -> str:
    """URL-safe random password for the generated local default."""
    return secrets.token_urlsafe(length)


# --- config objects ----------------------------------------------------------


@dataclass
class DashboardConfig:
    theme: str = DEFAULT_THEME
    language: str = DEFAULT_LANGUAGE

    def public_dict(self) -> dict[str, Any]:
        return {"theme": self.theme, "language": self.language}


@dataclass
class IntervalConfig:
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    menubar_seconds: int = DEFAULT_MENUBAR_SECONDS

    def public_dict(self) -> dict[str, Any]:
        return {"refresh_seconds": self.refresh_seconds, "menubar_seconds": self.menubar_seconds}


@dataclass
class AuthConfig:
    """Basic-auth settings for the dashboard and the local API.

    ``enabled`` is false by default: the server binds 127.0.0.1 and stays
    open for local use unless the user turns auth on.
    """

    enabled: bool = False
    username: str = DEFAULT_USERNAME
    password_hash: str | None = None
    password: str | None = None  # plaintext; local development only
    realm: str = DEFAULT_REALM

    @property
    def configured(self) -> bool:
        return bool(self.password_hash or self.password)

    def verify(self, username: str, password: str) -> bool:
        """Constant-time credential check. Always false when not configured."""
        if not self.configured:
            return False
        user_ok = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        if self.password_hash:
            password_ok = verify_password_hash(self.password_hash, password)
        else:
            password_ok = hmac.compare_digest(password.encode("utf-8"), str(self.password).encode("utf-8"))
        # Both checks always run so a wrong username costs the same as a wrong
        # password.
        return user_ok and password_ok


@dataclass
class AppConfig:
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    intervals: IntervalConfig = field(default_factory=IntervalConfig)
    path: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Everything the dashboard may know before authenticating.

        Deliberately excludes the username, the hash, and the realm.
        """
        return {"auth_required": self.auth.enabled, "dashboard": self.dashboard.public_dict(), "intervals": self.intervals.public_dict()}


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _dashboard_from(data: dict[str, Any]) -> DashboardConfig:
    raw = data.get("dashboard") if isinstance(data.get("dashboard"), dict) else {}
    theme = _clean(raw.get("theme")) or DEFAULT_THEME
    lang = _clean(os.environ.get("USAGE_MONITOR_LANGUAGE")) or _clean(raw.get("language")) or DEFAULT_LANGUAGE
    if lang.lower() in ("pt-br", "pt_br"):
        lang = "pt"
    return DashboardConfig(
        theme=theme if theme in ("light", "dark") else DEFAULT_THEME,
        language=lang if lang in ("en", "pt") else DEFAULT_LANGUAGE,
    )


def _intervals_from(data: dict[str, Any]) -> IntervalConfig:
    raw = data.get("intervals") if isinstance(data.get("intervals"), dict) else {}
    refresh_default = _as_int(raw.get("refresh_seconds"), DEFAULT_REFRESH_SECONDS)
    menubar_default = _as_int(raw.get("menubar_seconds"), DEFAULT_MENUBAR_SECONDS)
    return IntervalConfig(
        refresh_seconds=_as_int(os.environ.get("USAGE_MONITOR_REFRESH_INTERVAL"), refresh_default),
        menubar_seconds=_as_int(os.environ.get("USAGE_MONITOR_MENUBAR_INTERVAL"), menubar_default),
    )


def _auth_from(data: dict[str, Any]) -> AuthConfig:
    raw = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    env = os.environ
    username = _clean(env.get("USAGE_MONITOR_AUTH_USERNAME")) or _clean(raw.get("username")) or DEFAULT_USERNAME
    password_hash = _clean(env.get("USAGE_MONITOR_AUTH_PASSWORD_HASH")) or _clean(raw.get("password_hash"))
    password = _clean(env.get("USAGE_MONITOR_AUTH_PASSWORD")) or _clean(raw.get("password"))
    # An env-provided secret wins over the file; a file hash never overrides an
    # env password.
    if _clean(env.get("USAGE_MONITOR_AUTH_PASSWORD")) and not _clean(env.get("USAGE_MONITOR_AUTH_PASSWORD_HASH")):
        password_hash = None
    # Supplying a credential is itself an opt-in; `enabled` (file, then env)
    # can still force it either way.
    enabled = _as_bool(raw.get("enabled"), bool(password_hash or password))
    enabled = _as_bool(env.get("USAGE_MONITOR_AUTH_ENABLED"), enabled)
    return AuthConfig(
        enabled=enabled,
        username=username,
        password_hash=password_hash,
        password=password,
        realm=_clean(raw.get("realm")) or DEFAULT_REALM,
    )


def load_app_config(path: Path | None = None) -> AppConfig:
    """Load ``config.yaml``; a missing or unreadable file yields defaults."""
    target = Path(path).expanduser() if path else CONFIG_FILE
    try:
        data = _config_load(target)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return AppConfig(dashboard=_dashboard_from(data), auth=_auth_from(data), intervals=_intervals_from(data), path=str(target))


# --- writing config.yaml -----------------------------------------------------


def _write_config_data(path: Path, data: dict[str, Any]) -> Path:
    if yaml is None:  # pragma: no cover - PyYAML is a hard dependency
        raise RuntimeError("writing config.yaml requires PyYAML")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=True, default_flow_style=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8")
    # The file can hold a password hash: keep it owner-readable only.
    path.chmod(0o600)
    return path


def _read_config_data(path: Path) -> dict[str, Any]:
    try:
        data = _config_load(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def set_auth(
    path: Path | None = None,
    *,
    username: str = DEFAULT_USERNAME,
    password: str | None = None,
    enabled: bool = True,
) -> tuple[Path, str]:
    """Enable auth in ``config.yaml`` with a hashed password.

    Generates a password when none is given and returns it once so the caller
    can print it — it is never recoverable from the stored hash.
    """
    target = Path(path).expanduser() if path else CONFIG_FILE
    secret = password or generate_password()
    data = _read_config_data(target)
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    auth.update({"enabled": enabled, "username": username, "password_hash": hash_password(secret)})
    auth.pop("password", None)  # never keep a plaintext leftover next to a hash
    data["auth"] = auth
    return _write_config_data(target, data), secret


def disable_auth(path: Path | None = None) -> Path:
    """Turn auth off, keeping the stored username/hash for a later re-enable."""
    target = Path(path).expanduser() if path else CONFIG_FILE
    data = _read_config_data(target)
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    auth["enabled"] = False
    data["auth"] = auth
    return _write_config_data(target, data)


# --- request-side helpers ----------------------------------------------------


def parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    """Decode an ``Authorization: Basic`` header into (username, password)."""
    if not header:
        return None
    scheme, _, payload = header.partition(" ")
    if scheme.strip().lower() != "basic" or not payload.strip():
        return None
    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password

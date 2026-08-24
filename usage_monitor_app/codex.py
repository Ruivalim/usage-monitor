# SPDX-License-Identifier: MIT
"""OpenAI Codex / ChatGPT subscription usage (device-code OAuth + /wham/usage).

Reimplements the same public Codex CLI client used by Hermes:

* OAuth client: ``app_EMoamEEZ73f0CkXaXp7hrann``
* Device-code: ``auth.openai.com``
* Usage: ``GET https://chatgpt.com/backend-api/wham/usage``

Tokens can live in:

1. macOS Keychain (preferred for multi-sub) — JSON blob with access/refresh tokens
2. ``~/.codex/auth.json`` (Codex CLI session)
3. ``credential.source: literal`` for tests only

No Hermes dependency.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

from .core import Money, ProviderStatus, UsageWindow, _display_name, _http_get_json, _safe_float, _status_from_http

CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_ISSUER = "https://auth.openai.com"
CODEX_DEVICE_CODE_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/token"
DEFAULT_USAGE_BASE = "https://chatgpt.com/backend-api"
DEFAULT_KEYCHAIN_SERVICE = "api-usage-monitor/codex"
DEFAULT_KEYCHAIN_ACCOUNT = "default"
DEFAULT_CODEX_AUTH = Path.home() / ".codex" / "auth.json"
ACCESS_TOKEN_REFRESH_SKEW = 120
USER_AGENT = "codex-cli"


def _keychain_get(*, service: str, account: str) -> Optional[str]:
    cmd = ["security", "find-generic-password", "-s", service, "-a", account, "-w"]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=8)
        if proc.returncode == 0:
            token = proc.stdout.strip()
            return token or None
    except Exception:
        return None
    return None


def _keychain_set(*, service: str, account: str, password: str) -> None:
    # -U updates if present
    cmd = [
        "security",
        "add-generic-password",
        "-U",
        "-a",
        account,
        "-s",
        service,
        "-w",
        password,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=8)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "keychain write failed").strip()[:240])


def _parse_token_blob(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        data = json.loads(raw)
        if isinstance(data, dict) and "tokens" in data and isinstance(data["tokens"], dict):
            # ~/.codex/auth.json shape
            tokens = dict(data["tokens"])
            tokens.setdefault("last_refresh", data.get("last_refresh"))
            return tokens
        return data if isinstance(data, dict) else {}
    # bare access token
    return {"access_token": raw}


def _jwt_exp(access_token: str) -> Optional[float]:
    try:
        import base64

        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def _needs_refresh(tokens: dict[str, Any]) -> bool:
    access = str(tokens.get("access_token") or "")
    if not access:
        return True
    exp = _jwt_exp(access)
    if exp is None:
        return False
    return exp <= time.time() + ACCESS_TOKEN_REFRESH_SKEW


def refresh_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    if httpx is None:
        raise RuntimeError("httpx is not available")
    refresh = str(tokens.get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError("Codex session missing refresh_token — run: usagemon codex-login")
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json", "User-Agent": USER_AGENT}) as client:
        resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Codex token refresh failed HTTP {resp.status_code}: {resp.text[:180]}")
    body = resp.json() or {}
    out = dict(tokens)
    if body.get("access_token"):
        out["access_token"] = body["access_token"]
    if body.get("refresh_token"):
        out["refresh_token"] = body["refresh_token"]
    if body.get("id_token"):
        out["id_token"] = body["id_token"]
    out["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return out


def _auth_paths(conf: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for key in ("auth_path", "auth_file", "codex_auth"):
        raw = conf.get(key)
        if isinstance(raw, str) and raw.strip():
            paths.append(Path(raw).expanduser())
    env = os.environ.get("USAGE_MONITOR_CODEX_AUTH_FILE") or os.environ.get("CODEX_AUTH_FILE")
    if env:
        paths.append(Path(env).expanduser())
    if not paths:
        home = conf.get("codex_home")
        if isinstance(home, str) and home.strip():
            paths.append(Path(home).expanduser() / "auth.json")
        paths.append(DEFAULT_CODEX_AUTH)
    # dedup
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        k = str(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _load_tokens(conf: dict[str, Any]) -> tuple[dict[str, Any], str, Optional[str]]:
    """Return (tokens, source, persist_hint).

    ``persist_hint`` is ``keychain:service:account`` or a filesystem path string
    when tokens should be written back after refresh.
    """
    # 1) Explicit credential
    cred = conf.get("credential") if isinstance(conf.get("credential"), dict) else {}
    source = str(cred.get("source") or "")
    if source == "keychain":
        service = str(cred.get("service") or conf.get("id") or DEFAULT_KEYCHAIN_SERVICE)
        account = str(cred.get("account") or DEFAULT_KEYCHAIN_ACCOUNT)
        raw = _keychain_get(service=service, account=account)
        if raw:
            return _parse_token_blob(raw), f"keychain:{service}", f"keychain:{service}:{account}"
    elif source == "literal":
        raw = str(cred.get("value") or "")
        if raw:
            return _parse_token_blob(raw), "config", None
    elif source == "env":
        raw = os.environ.get(str(cred.get("name") or ""), "")
        if raw:
            return _parse_token_blob(raw), "env", None
    elif source == "file":
        raw_path = str(cred.get("path") or "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if path.is_file():
                try:
                    raw = path.read_text(encoding="utf-8").strip()
                except OSError:
                    raw = ""
                if raw:
                    return _parse_token_blob(raw), f"file:{path}", None

    # 2) Default keychain for this provider id
    service = str(conf.get("keychain_service") or DEFAULT_KEYCHAIN_SERVICE)
    account = str(conf.get("keychain_account") or conf.get("id") or DEFAULT_KEYCHAIN_ACCOUNT)
    raw = _keychain_get(service=service, account=account)
    if raw:
        return _parse_token_blob(raw), f"keychain:{service}", f"keychain:{service}:{account}"

    # 3) Codex CLI auth.json
    for path in _auth_paths(conf):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        tokens = _parse_token_blob(json.dumps(data))
        if tokens.get("access_token") or tokens.get("refresh_token"):
            return tokens, f"codex-auth:{path}", str(path)

    return {}, "none", None


def _persist_tokens(tokens: dict[str, Any], persist_hint: Optional[str]) -> None:
    if not persist_hint:
        return
    blob = json.dumps(
        {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
            "account_id": tokens.get("account_id"),
            "last_refresh": tokens.get("last_refresh"),
        },
        separators=(",", ":"),
    )
    if persist_hint.startswith("keychain:"):
        # keychain:service:account
        parts = persist_hint.split(":", 2)
        if len(parts) == 3:
            _, service, account = parts
            _keychain_set(service=service, account=account, password=blob)
        return
    path = Path(persist_hint)
    try:
        existing: dict[str, Any] = {}
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        existing.setdefault("auth_mode", "chatgpt")
        existing["tokens"] = {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
            "account_id": tokens.get("account_id"),
        }
        existing["last_refresh"] = tokens.get("last_refresh")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def usage_url(conf: dict[str, Any] | None = None) -> str:
    conf = conf or {}
    base = str(
        conf.get("base_url")
        or conf.get("usage_base")
        or os.environ.get("USAGE_MONITOR_CODEX_BASE_URL")
        or DEFAULT_USAGE_BASE
    ).rstrip("/")
    if base.endswith("/codex"):
        base = base[: -len("/codex")]
    if "/backend-api" in base:
        return f"{base}/wham/usage"
    return f"{base}/api/codex/usage"


def _parse_reset_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # epoch seconds
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # numeric string epoch
        if text.isdigit() or (text.replace(".", "", 1).isdigit() and text.count(".") <= 1):
            return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return text


def fetch_usage(tokens: dict[str, Any], conf: dict[str, Any] | None = None) -> ProviderStatus:
    conf = conf or {}
    conf_id = str(conf.get("id") or "codex")
    label = _display_name(conf, "Codex")
    access = str(tokens.get("access_token") or "").strip()
    if not access:
        return ProviderStatus(conf_id, label, status="unavailable", source="codex", message="No Codex access_token")
    account_id = str(tokens.get("account_id") or conf.get("account_id") or "").strip()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    url = usage_url(conf)
    try:
        code, body = _http_get_json(url, access, timeout=float(conf.get("timeout", 15.0)), headers=headers)
    except Exception as exc:
        return ProviderStatus(conf_id, label, status="unavailable", source="codex", message=str(exc)[:240])
    status, msg = _status_from_http(code, body)
    ps = ProviderStatus(conf_id, label, status=status, source="codex", message=msg)
    if not (200 <= code < 300) or not isinstance(body, dict):
        if code in (401, 403):
            ps.message = "Codex session rejected — run: usagemon codex-login"
        return ps

    plan = body.get("plan_type") or body.get("planType")
    if plan:
        ps.details.append(f"plan: {str(plan).replace('_', ' ').title()}")

    rate_limit = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    for key, window_label in (("primary_window", "Current session"), ("secondary_window", "Current week")):
        window = rate_limit.get(key) if isinstance(rate_limit.get(key), dict) else {}
        used = _safe_float(window.get("used_percent"))
        if used is None:
            continue
        used = max(0.0, min(100.0, used))
        ps.windows.append(
            UsageWindow(
                label=window_label,
                used_percent=round(used, 2),
                remaining_percent=round(max(0.0, 100.0 - used), 2),
                reset_at=_parse_reset_at(window.get("reset_at") or window.get("resetAt")),
            )
        )

    reset_credits = body.get("rate_limit_reset_credits") if isinstance(body.get("rate_limit_reset_credits"), dict) else {}
    banked = reset_credits.get("available_count")
    if isinstance(banked, (int, float)) and int(banked) > 0:
        n = int(banked)
        ps.details.append(f"banked resets: {n}")

    credits = body.get("credits") if isinstance(body.get("credits"), dict) else {}
    if credits.get("has_credits"):
        bal = _safe_float(credits.get("balance"))
        if bal is not None:
            ps.balance = Money(round(bal, 4), "USD")
            ps.details.append(f"extra credits: {bal:.2f} USD")
        elif credits.get("unlimited"):
            ps.details.append("credits: unlimited")

    if not ps.windows:
        ps.status = "unknown"
        ps.message = ps.message or "Codex usage response had no rate-limit windows"
        return ps

    worst = max((w.used_percent or 0.0) for w in ps.windows)
    if worst >= 100:
        ps.status = "quota_exhausted"
        ps.message = ps.message or "Codex usage exhausted"
    elif worst >= float(conf.get("warn_percent") or 85) and ps.status == "ok":
        ps.status = "warning"
        ps.message = ps.message or "Codex usage nearly exhausted"
    return ps


def check_provider(conf: dict[str, Any]) -> ProviderStatus:
    try:
        from . import plog
    except Exception:
        plog = None  # type: ignore

    conf_id = str(conf.get("id") or "codex")
    label = _display_name(conf, "Codex")
    tokens, source, persist_hint = _load_tokens(conf)
    if plog:
        plog.debug("token source", source=source, has_access=bool(tokens.get("access_token")), has_refresh=bool(tokens.get("refresh_token")))
    if not tokens.get("access_token") and not tokens.get("refresh_token"):
        if plog:
            plog.warning("no codex session")
        return ProviderStatus(
            conf_id,
            label,
            status="unavailable",
            source=source,
            message="No Codex session — run: usagemon codex-login (or log in with `codex`)",
        )
    try:
        if _needs_refresh(tokens) and tokens.get("refresh_token"):
            if plog:
                plog.info("refreshing access token")
            tokens = refresh_tokens(tokens)
            _persist_tokens(tokens, persist_hint)
    except Exception as exc:
        if plog:
            plog.exception("token refresh failed", exc)
        # Try with existing access token; if refresh failed hard, still attempt usage.
        if not tokens.get("access_token"):
            return ProviderStatus(conf_id, label, status="error", source=source, message=str(exc)[:240])
    if plog:
        plog.debug("fetch usage", url=usage_url(conf))
    ps = fetch_usage(tokens, conf)
    if ps.source == "codex":
        ps.source = source if source != "none" else "codex"
    return ps


def device_code_login(*, store_keychain: bool = True, service: str = DEFAULT_KEYCHAIN_SERVICE, account: str = DEFAULT_KEYCHAIN_ACCOUNT) -> dict[str, Any]:
    """Interactive device-code login. Prints URL + code; blocks until approved."""
    if httpx is None:
        raise RuntimeError("httpx is not available")

    # Step 1: device code
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(CODEX_DEVICE_CODE_URL, json={"client_id": CODEX_OAUTH_CLIENT_ID}, headers={"Content-Type": "application/json"})
        if resp.status_code == 429:
            raise RuntimeError("OpenAI is rate-limiting login (HTTP 429). Wait a minute and try again.")
        if resp.status_code != 200:
            raise RuntimeError(f"Device code request failed HTTP {resp.status_code}: {resp.text[:200]}")
        device_data = resp.json() or {}
        user_code = str(device_data.get("user_code") or "")
        device_auth_id = str(device_data.get("device_auth_id") or "")
        poll_interval = max(3, int(device_data.get("interval") or 5))
        if not user_code or not device_auth_id:
            raise RuntimeError("Device code response incomplete")

        print("To continue, follow these steps:\n")
        print("  1. Open this URL in your browser:")
        print(f"     {CODEX_ISSUER}/codex/device\n")
        print("  2. Enter this code:")
        print(f"     {user_code}\n")
        print("Waiting for sign-in... (Ctrl+C to cancel)")

        # Step 2: poll
        max_wait = 15 * 60
        start = time.monotonic()
        code_resp = None
        try:
            while time.monotonic() - start < max_wait:
                time.sleep(poll_interval)
                poll = client.post(
                    CODEX_DEVICE_TOKEN_URL,
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    code_resp = poll.json() or {}
                    break
                if poll.status_code in (403, 404):
                    continue
                raise RuntimeError(f"Device auth poll failed HTTP {poll.status_code}")
        except KeyboardInterrupt as exc:
            raise SystemExit(130) from exc
        if not code_resp:
            raise RuntimeError("Login timed out after 15 minutes")

        authorization_code = str(code_resp.get("authorization_code") or "")
        code_verifier = str(code_resp.get("code_verifier") or "")
        if not authorization_code or not code_verifier:
            raise RuntimeError("Device auth missing authorization_code/code_verifier")

        # Step 3: exchange
        token_resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": f"{CODEX_ISSUER}/deviceauth/callback",
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code >= 400:
            raise RuntimeError(f"Token exchange failed HTTP {token_resp.status_code}: {token_resp.text[:200]}")
        body = token_resp.json() or {}

    tokens = {
        "access_token": body.get("access_token"),
        "refresh_token": body.get("refresh_token"),
        "id_token": body.get("id_token"),
        "account_id": body.get("account_id") or _account_id_from_id_token(body.get("id_token")),
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if not tokens.get("access_token"):
        raise RuntimeError("Token exchange returned no access_token")

    if store_keychain:
        _persist_tokens(tokens, f"keychain:{service}:{account}")
        print(f"\nSaved Codex session to Keychain service={service!r} account={account!r}")
    return tokens


def ensure_providers_entry(
    *,
    service: str = DEFAULT_KEYCHAIN_SERVICE,
    account: str = DEFAULT_KEYCHAIN_ACCOUNT,
    provider_id: str = "codex",
    name: str = "Codex",
    providers_file: Path | None = None,
) -> tuple[Path, str]:
    """Ensure a ``type: codex`` entry exists in providers.yaml.

    Returns ``(path, action)`` where action is ``added`` | ``updated`` | ``unchanged``.
    """
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to update providers.yaml") from exc

    from .core import PROVIDERS_FILE

    path = Path(providers_file).expanduser() if providers_file else PROVIDERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file() and path.read_text(encoding="utf-8").strip():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
    else:
        data = {"defaults": {"timeout": 12}, "providers": []}

    providers = data.get("providers")
    if not isinstance(providers, list):
        providers = []
        data["providers"] = providers

    desired_cred = {
        "source": "keychain",
        "service": service,
        "account": account,
    }

    for item in providers:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != provider_id and str(item.get("type") or "") not in ("codex", "openai-codex"):
            continue
        # Match by id or any codex type with same account/service if id matches preferred.
        if str(item.get("id") or "") != provider_id:
            continue
        changed = False
        if item.get("type") not in ("codex", "openai-codex"):
            item["type"] = "codex"
            changed = True
        if not item.get("name") and not item.get("label"):
            item["name"] = name
            changed = True
        if item.get("enabled") is False:
            item["enabled"] = True
            changed = True
        cred = item.get("credential") if isinstance(item.get("credential"), dict) else {}
        if (
            cred.get("source") != "keychain"
            or str(cred.get("service") or "") != service
            or str(cred.get("account") or "") != account
        ):
            item["credential"] = desired_cred
            changed = True
        if changed:
            path.write_text(
                yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            return path, "updated"
        return path, "unchanged"

    providers.append(
        {
            "id": provider_id,
            "name": name,
            "type": "codex",
            "credential": desired_cred,
        }
    )
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path, "added"


def _account_id_from_id_token(id_token: Any) -> Optional[str]:
    if not id_token or not isinstance(id_token, str):
        return None
    try:
        import base64

        parts = id_token.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        # ChatGPT account id fields vary; try common keys.
        for key in ("chatgpt_account_id", "https://api.openai.com/auth", "account_id", "sub"):
            val = payload.get(key)
            if isinstance(val, dict):
                aid = val.get("chatgpt_account_id") or val.get("account_id")
                if aid:
                    return str(aid)
            elif val and key != "sub":
                return str(val)
        auth = payload.get("https://api.openai.com/auth")
        if isinstance(auth, dict) and auth.get("chatgpt_account_id"):
            return str(auth["chatgpt_account_id"])
    except Exception:
        return None
    return None

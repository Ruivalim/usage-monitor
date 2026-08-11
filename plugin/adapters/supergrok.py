# SPDX-License-Identifier: MIT
"""
SuperGrok / X Premium+ weekly usage pool.

Reads the Grok Build CLI OAuth session from ``~/.grok/auth.json`` (written by
``grok login``) and calls the same unofficial billing endpoint the CLI uses:

    GET https://cli-chat-proxy.grok.com/v1/billing?format=credits

Headers match the Grok CLI:

    Authorization: Bearer <session key>
    X-XAI-Token-Auth: xai-grok-cli
    x-userid: <user id from auth.json>

Reports a single usage window (current week/month pool percent) plus optional
product breakdown and Extra Usage Credits balance.

This is **not** the developer Management API (prepaid API credits). That path
is the built-in ``xai`` provider type.

Requires: a logged-in Grok CLI session (``grok login``). The CLI only refreshes
its access token at startup, so this adapter runs the same OIDC refresh when the
stored token is expiring and writes the rotated tokens back to ``auth.json``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from usage_monitor import ProviderStatus, UsageWindow  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from usage_monitor import ProviderStatus, UsageWindow
    except ImportError:
        ProviderStatus = None  # type: ignore
        UsageWindow = None  # type: ignore


DEFAULT_AUTH = Path.home() / ".grok" / "auth.json"
DEFAULT_BASE = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
REFRESH_SKEW = 120.0
PROVIDER_ID = "supergrok"
LABEL = "SuperGrok"


def _expiry(entry):
    raw = entry.get("expires_at") or entry.get("expiresAt")
    if not raw:
        return None
    try:
        exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp.timestamp()


def _refresh(auth_path, key, entry):
    """Public-client OIDC refresh, persisted back so the Grok CLI keeps working.

    Returns ``(entry, error)``.
    """
    refresh_token = str(entry.get("refresh_token") or entry.get("refreshToken") or "").strip()
    client_id = str(entry.get("oidc_client_id") or entry.get("client_id") or "").strip()
    if not client_id and "::" in key:
        client_id = key.rsplit("::", 1)[1].strip()
    if not refresh_token or not client_id:
        return None, "Grok session expired and cannot be refreshed — run `grok login`"
    issuer = str(entry.get("oidc_issuer") or "").strip().rstrip("/")
    endpoint = os.environ.get("USAGE_MONITOR_GROK_TOKEN_ENDPOINT") or (
        f"{issuer}/oauth2/token" if issuer else DEFAULT_TOKEN_ENDPOINT
    )
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode()
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return None, "Grok refresh token rejected — run `grok login`"
        return None, f"Grok token refresh HTTP {e.code}"
    except Exception as exc:
        return None, f"Grok token refresh failed: {exc}"

    access = str((body or {}).get("access_token") or "").strip()
    if not access:
        return None, "Grok token refresh returned no access_token"

    updated = dict(entry)
    updated["key"] = access
    rotated = str((body or {}).get("refresh_token") or "").strip()
    if rotated:
        updated["refresh_token"] = rotated
    try:
        expires_in = float((body or {}).get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in:
        stamp = datetime.fromtimestamp(time.time() + expires_in, timezone.utc)
        updated["expires_at"] = stamp.replace(tzinfo=None).isoformat() + "Z"
    _persist(auth_path, key, updated)
    return updated, None


def _persist(auth_path, key, entry):
    """Atomically store the refreshed entry, yielding to a newer CLI session."""
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        current = data.get(key)
        if isinstance(current, dict) and (_expiry(current) or 0.0) > (_expiry(entry) or 0.0):
            return
        data[key] = entry
        tmp = auth_path.with_name(auth_path.name + ".usage-monitor.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, auth_path)
    except Exception:
        pass


def check():
    def result(status, message=None, source="grok-auth", **extra):
        out = {"id": PROVIDER_ID, "label": LABEL, "status": status, "source": source}
        if message:
            out["message"] = message
        out.update(extra)
        return out

    auth_path = Path(
        os.environ.get("USAGE_MONITOR_GROK_AUTH_FILE")
        or os.environ.get("GROK_AUTH_FILE")
        or DEFAULT_AUTH
    ).expanduser()
    if not auth_path.is_file():
        return result("unavailable", f"Grok auth not found ({auth_path}) — run `grok login`")

    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return result("unavailable", f"Failed to read Grok auth: {exc}")

    entry = entry_key = None
    if isinstance(data, dict):
        for candidate_key, value in data.items():
            if isinstance(value, dict) and (value.get("key") or value.get("access_token")):
                entry, entry_key = value, str(candidate_key)
                break
    if not entry:
        return result("unavailable", "No session in Grok auth.json — run `grok login`")

    # The CLI refreshes only at startup, so the stored token is routinely
    # expired here while the login itself is still good.
    exp = _expiry(entry)
    if exp is not None and exp <= time.time() + REFRESH_SKEW:
        refreshed, err = _refresh(auth_path, entry_key, entry)
        if refreshed is None:
            return result("unavailable", err or "Grok session expired — run `grok login`")
        entry = refreshed

    token = str(entry.get("key") or entry.get("access_token") or "").strip()
    user_id = str(entry.get("user_id") or entry.get("userId") or entry.get("principal_id") or "").strip()
    if not token or not user_id:
        return result("unavailable", "Incomplete Grok session — run `grok login`")

    base = (
        os.environ.get("USAGE_MONITOR_GROK_PROXY_BASE")
        or os.environ.get("GROK_CLI_CHAT_PROXY_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    url = f"{base}/billing?format=credits"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-userid": user_id,
        "x-grok-client-version": os.environ.get("USAGE_MONITOR_GROK_CLIENT_VERSION") or "usage-monitor",
        "User-Agent": "xai-grok-cli",
    }

    def fetch():
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode())

    try:
        try:
            body = fetch()
        except urllib.error.HTTPError as e:
            # Token may have been revoked or rotated under us; refresh once.
            if e.code not in (401, 403):
                raise
            refreshed, _ = _refresh(auth_path, entry_key, entry)
            if not refreshed:
                raise
            headers["Authorization"] = f"Bearer {refreshed['key']}"
            body = fetch()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return result("error", "Grok session rejected — run `grok login`")
        if e.code == 429:
            return result("rate_limited", "SuperGrok billing endpoint rate-limited")
        return result("warning", f"SuperGrok billing HTTP {e.code}")
    except Exception as exc:
        return result("unavailable", f"SuperGrok billing unreachable: {exc}")

    cfg = body.get("config") if isinstance(body, dict) and isinstance(body.get("config"), dict) else {}
    if not cfg:
        return result("unknown", "Billing response missing config")

    used_pct = cfg.get("creditUsagePercent")
    try:
        used_pct = float(used_pct) if used_pct is not None else None
    except (TypeError, ValueError):
        used_pct = None

    period = cfg.get("currentPeriod") if isinstance(cfg.get("currentPeriod"), dict) else {}
    ptype = str(period.get("type") or "")
    window_label = "Current month" if "MONTH" in ptype.upper() else "Current week"
    reset_at = period.get("end") or cfg.get("billingPeriodEnd")

    windows = []
    status = "ok"
    message = None
    if used_pct is not None:
        used_pct = max(0.0, min(100.0, used_pct))
        windows.append({
            "label": window_label,
            "used_percent": round(used_pct, 2),
            "remaining_percent": round(max(0.0, 100.0 - used_pct), 2),
            "reset_at": reset_at,
        })
        if used_pct >= 100:
            status = "quota_exhausted"
            message = "Weekly SuperGrok usage exhausted"
        elif used_pct >= 85:
            status = "warning"
            message = "Weekly SuperGrok usage nearly exhausted"
    else:
        status = "unknown"
        message = "No creditUsagePercent in billing response"

    details = []
    for item in cfg.get("productUsage") or []:
        if isinstance(item, dict) and item.get("product") is not None:
            pct = item.get("usagePercent")
            details.append(f"{item.get('product')}: {pct}% of pool" if pct is not None else str(item.get("product")))
    if cfg.get("isUnifiedBillingUser"):
        details.append("unified weekly pool")
    prepaid = cfg.get("prepaidBalance") if isinstance(cfg.get("prepaidBalance"), dict) else None
    balance = None
    if prepaid and prepaid.get("val") is not None:
        try:
            amount = float(prepaid["val"]) / 100.0
            if amount > 0:
                balance = {"amount": round(amount, 4), "currency": "USD"}
                details.append(f"extra credits: {amount:.2f} USD")
        except (TypeError, ValueError):
            pass

    out = result(status, message, source=f"grok-auth:{auth_path}", windows=windows, details=details[:12])
    if balance:
        out["balance"] = balance
    return out

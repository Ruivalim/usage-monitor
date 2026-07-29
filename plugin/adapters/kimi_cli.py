# SPDX-License-Identifier: MIT
"""
Kimi Code subscription usage — via the managed OAuth `/usages` endpoint.

The Kimi CLI stores OAuth credentials at ~/.kimi-code/credentials/kimi-code.json.
Since `/usage` only works in interactive mode (not `-p`), this adapter uses the
OAuth token directly against the same endpoint the CLI uses:

    GET https://api.kimi.com/coding/v1/usages

Access tokens expire quickly (expires_in ~900s), so expired tokens are
refreshed first via the OAuth host, exactly like the CLI does:

    POST https://auth.kimi.com/api/oauth/token
    client_id=<kimi-code client id>&grant_type=refresh_token&refresh_token=...

Refreshed credentials are written back to the credentials file because the
refresh token may rotate.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Allow running standalone or as Hermes plugin adapter
try:
    from usage_monitor import ProviderStatus  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from usage_monitor import ProviderStatus
    except ImportError:
        ProviderStatus = None  # type: ignore


CREDENTIAL_PATH = Path.home() / ".kimi-code" / "credentials" / "kimi-code.json"
OAUTH_TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"  # public kimi-code CLI client
DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
TOKEN_SKEW_SECONDS = 60
FIXED_POINT_CENTS = 1_000_000  # boosterWallet amounts are fixed-point micro-cents


def check():
    label = "Kimi Coding"
    provider_id = "kimi-coding"

    def result(status, message=None, source="cli", **extra):
        out = {"id": provider_id, "label": label, "status": status, "source": source}
        if message:
            out["message"] = message
        out.update(extra)
        return out

    if not CREDENTIAL_PATH.exists():
        return result("unavailable", "Kimi CLI credentials not found")

    try:
        creds = json.loads(CREDENTIAL_PATH.read_text())
    except Exception as exc:
        return result("unavailable", f"Failed to read Kimi credentials: {exc}")

    access_token = creds.get("access_token")
    if not access_token:
        return result("unavailable", "No access token in Kimi credentials")

    # Access tokens are short-lived (~15 min); refresh when expired.
    expires_at = creds.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at <= time.time() + TOKEN_SKEW_SECONDS:
        creds, error = _refresh_token(creds)
        if error:
            return result("error", error)
        access_token = creds.get("access_token")

    base_url = (os.environ.get("KIMI_CODE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/usages"

    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        body = json.loads(resp.read().decode())
        return _parse_kimi_response(result, body)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return result("error", "Kimi token rejected — run `kimi` and /login again")
        if e.code == 429:
            return result("rate_limited", "Kimi usage endpoint rate-limited the request")
        return result("warning", f"Kimi usage endpoint returned HTTP {e.code}")
    except Exception as exc:
        return result("warning", f"Kimi usage endpoint unreachable: {exc}")


def _refresh_token(creds: dict):
    """Refresh the access token like the CLI does. Returns (creds, error)."""
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        return creds, "Kimi token expired and no refresh_token available — run `kimi` and /login again"

    payload = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()

    try:
        req = urllib.request.Request(OAUTH_TOKEN_URL, data=payload, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {}
        if e.code in (401, 403) or detail.get("error") == "invalid_grant":
            return creds, "Kimi refresh token rejected — run `kimi` and /login again"
        return creds, f"Kimi token refresh failed (HTTP {e.code})"
    except Exception as exc:
        return creds, f"Kimi token refresh failed: {exc}"

    if not isinstance(data.get("access_token"), str):
        return creds, "Kimi token refresh returned no access_token"

    creds = dict(creds)
    creds["access_token"] = data["access_token"]
    if isinstance(data.get("refresh_token"), str):
        creds["refresh_token"] = data["refresh_token"]
    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)):
        creds["expires_in"] = expires_in
        creds["expires_at"] = time.time() + expires_in

    # Persist because the refresh token may have rotated.
    try:
        CREDENTIAL_PATH.write_text(json.dumps(creds, indent=2))
        os.chmod(CREDENTIAL_PATH, 0o600)
    except Exception:
        pass

    return creds, None


def _to_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _usage_row(raw, default_label):
    """Mirror of the CLI's toUsageRow: {label, used, limit, reset_at}."""
    if not isinstance(raw, dict):
        return None
    limit = _to_int(raw.get("limit"))
    used = _to_int(raw.get("used"))
    if used is None:
        remaining = _to_int(raw.get("remaining"))
        if remaining is not None and limit is not None:
            used = limit - remaining
    if used is None and limit is None:
        return None
    label = raw.get("name") or raw.get("title") or default_label
    reset_at = None
    for key in ("reset_at", "resetAt", "reset_time", "resetTime"):
        if isinstance(raw.get(key), str) and raw[key]:
            reset_at = raw[key]
            break
    return {"label": str(label), "used": used or 0, "limit": limit or 0, "reset_at": reset_at}


def _limit_label(item, detail, window, idx):
    for key in ("name", "title", "scope"):
        v = item.get(key) or detail.get(key)
        if isinstance(v, str) and v:
            return v
    duration = _to_int(window.get("duration") or item.get("duration") or detail.get("duration"))
    unit = window.get("timeUnit") or item.get("timeUnit") or detail.get("timeUnit") or ""
    if duration is not None:
        if "MINUTE" in unit:
            if duration >= 60 and duration % 60 == 0:
                return f"{duration // 60}h limit"
            return f"{duration}m limit"
        if "HOUR" in unit:
            return f"{duration}h limit"
        if "DAY" in unit:
            return f"{duration}d limit"
        return f"{duration}s limit"
    return f"Limit #{idx + 1}"


def _window_from_row(row):
    used, limit = row["used"], row["limit"]
    used_percent = round(100.0 * used / limit, 1) if limit > 0 else None
    window = {
        "label": row["label"],
        "used_percent": used_percent,
        "remaining_percent": round(100.0 - used_percent, 1) if used_percent is not None else None,
        "reset_at": row.get("reset_at"),
    }
    if limit > 0:
        window["detail"] = f"{used}/{limit}"
    return window


def _parse_kimi_response(result, data):
    """Parse the /usages payload (same shape the CLI parses) into provider status."""
    payload = data if isinstance(data, dict) else {}

    windows = []
    summary = _usage_row(payload.get("usage"), "Weekly limit")
    if summary:
        windows.append(_window_from_row(summary))

    raw_limits = payload.get("limits")
    if isinstance(raw_limits, list):
        for idx, item in enumerate(raw_limits):
            if not isinstance(item, dict):
                continue
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
            window = item.get("window") if isinstance(item.get("window"), dict) else {}
            row = _usage_row(detail, _limit_label(item, detail, window, idx))
            if row:
                if not row.get("reset_at"):
                    row["reset_at"] = None
                windows.append(_window_from_row(row))

    # Extra Usage ("booster") wallet — amounts are fixed-point micro-cents.
    balance = None
    details = []
    booster = payload.get("boosterWallet")
    if isinstance(booster, dict):
        b = booster.get("balance")
        if isinstance(b, dict) and b.get("type") == "BOOSTER":
            amount_left = _to_int(b.get("amountLeft")) or 0
            money = booster.get("monthlyChargeLimit") or booster.get("monthlyUsed") or {}
            currency = money.get("currency") if isinstance(money, dict) else None
            balance = {"amount": amount_left / FIXED_POINT_CENTS / 100, "currency": str(currency or "USD")}
            monthly_used = booster.get("monthlyUsed")
            if isinstance(monthly_used, dict):
                used_cents = _to_int(monthly_used.get("priceInCents"))
                if used_cents is not None:
                    details.append(f"extra usage this month: {used_cents / 100:.2f} {balance['currency']}")

    exhausted = any(w.get("used_percent") is not None and w["used_percent"] >= 100 for w in windows)
    status = "quota_exhausted" if exhausted and not (balance and balance["amount"] > 0) else "ok"

    extra = {"windows": windows}
    if balance:
        extra["balance"] = balance
    if details:
        extra["details"] = details
    return result(status, source="api", **extra)

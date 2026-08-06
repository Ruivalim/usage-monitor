# SPDX-License-Identifier: MIT
"""
Qwen Token Plan subscription usage — via local usage logs + API reachability.

The Qwen Token Plan (https://www.qwencloud.com/pricing/token-plan) uses
Credits as a unified billing unit.  There is no public API to query remaining
credits; the console web UI is the only official source.

This adapter works around that by:

1. Reading the Qwen CLI's local per-request usage logs at
   ~/.qwen/usage/token-usage-YYYY-MM.jsonl
2. Aggregating tokens over the current 7-day billing window
3. Optionally converting to Credits when USAGE_MONITOR_QWEN_CREDITS_PER_M_TOKEN
   is set (calibrate once by comparing local totals with the console)
4. Checking API reachability via the /models endpoint

Plan limits (7-day window):
  Lite:     2,500 credits    Standard:  10,000 credits
  Pro:     40,000 credits    Credit Pack: 20,000 credits

Environment variables:
  USAGE_MONITOR_QWEN_API_KEY        Override API key (sk-sp-...)
  USAGE_MONITOR_QWEN_BASE_URL       Override base URL
  USAGE_MONITOR_QWEN_HOME           Override ~/.qwen/ path
  USAGE_MONITOR_QWEN_USD_PER_CREDIT USD cost of 1 credit. Calibrate by
                                    comparing local cost with console
                                    credits. Default: None (no conversion).
  USAGE_MONITOR_QWEN_CREDIT_LIMIT   7-day credit limit (default: 2500 = Lite)
  USAGE_MONITOR_QWEN_WINDOW_DAYS    Window size in days (default: 7)

Model pricing (USD per million tokens) is configured per-model in
_MODEL_PRICING below. Override or extend via USAGE_MONITOR_QWEN_PRICING_JSON:
  '{"qwen3.8-max":{"in":2.0,"out":6.0},"qwen3.7-plus":{"in":0.50,"out":2.0}}'
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from usage_monitor import ProviderStatus  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from usage_monitor import ProviderStatus
    except ImportError:
        ProviderStatus = None  # type: ignore


DEFAULT_BASE_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_CREDIT_LIMIT = 2500  # Lite plan 7-day window
DEFAULT_WINDOW_DAYS = 7

# Per-model pricing in USD per million tokens.
# Source: https://www.qwencloud.com/pricing/token-plan and public pricing pages.
# Input prices use the base tier (≤256K context). Output prices are for
# standard (non-thinking) mode. Thinking tokens are billed at output rate.
_MODEL_PRICING: dict[str, dict[str, float]] = {
    # Qwen Max tier
    "qwen3.8-max": {"in": 2.0, "out": 6.0},
    "qwen3.8-max-preview": {"in": 2.0, "out": 6.0},
    "qwen3.7-max": {"in": 2.0, "out": 6.0},
    # Qwen Plus tier
    "qwen3.7-plus": {"in": 0.50, "out": 2.0},
    "qwen3.6-plus": {"in": 0.50, "out": 2.0},
    "qwen3.5-plus": {"in": 0.50, "out": 2.0},
    # Qwen Flash tier (much cheaper)
    "qwen3.7-flash": {"in": 0.05, "out": 0.20},
    "qwen3.6-flash": {"in": 0.05, "out": 0.20},
    # Third-party models on Token Plan (approximate — same pricing tier as Plus)
    "deepseek-v4-pro": {"in": 0.50, "out": 2.0},
    "deepseek-v4-flash": {"in": 0.05, "out": 0.20},
    "deepseek-v3.2": {"in": 0.50, "out": 2.0},
    "kimi-k2.7-code": {"in": 0.50, "out": 2.0},
    "kimi-k2.6": {"in": 0.50, "out": 2.0},
    "kimi-k2.5": {"in": 0.50, "out": 2.0},
    "glm-5.2": {"in": 0.50, "out": 2.0},
    "glm-5.1": {"in": 0.50, "out": 2.0},
    "glm-5": {"in": 0.50, "out": 2.0},
    "MiniMax-M2.5": {"in": 0.50, "out": 2.0},
}

# Plan tiers for auto-detection based on credit limit
_PLAN_TIERS = {
    2500: "Lite",
    10000: "Standard",
    40000: "Pro",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _load_pricing() -> dict[str, dict[str, float]]:
    """Return per-model pricing, merged with any env override."""
    pricing = dict(_MODEL_PRICING)
    raw = os.environ.get("USAGE_MONITOR_QWEN_PRICING_JSON")
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                for model, rates in override.items():
                    if isinstance(rates, dict) and "in" in rates:
                        pricing[model] = {"in": float(rates["in"]), "out": float(rates.get("out", 0))}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return pricing


def _qwen_home() -> Path:
    raw = os.environ.get("USAGE_MONITOR_QWEN_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".qwen"


def _read_api_key() -> str | None:
    env_key = os.environ.get("USAGE_MONITOR_QWEN_API_KEY")
    if env_key:
        return env_key
    settings_path = _qwen_home() / "settings.json"
    if not settings_path.exists():
        return None
    try:
        settings = json.loads(settings_path.read_text())
    except Exception:
        return None
    env_block = settings.get("env") or {}
    return (
        env_block.get("BAILIAN_TOKEN_PLAN_API_KEY")
        or env_block.get("BAILIAN_CODING_PLAN_API_KEY")
    )


def _read_base_url() -> str:
    env_url = os.environ.get("USAGE_MONITOR_QWEN_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    settings_path = _qwen_home() / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            providers = settings.get("modelProviders") or {}
            for provider_list in providers.values():
                if isinstance(provider_list, list):
                    for m in provider_list:
                        if isinstance(m, dict) and m.get("envKey") == "BAILIAN_TOKEN_PLAN_API_KEY":
                            return (m.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
        except Exception:
            pass
    return DEFAULT_BASE_URL


def _load_usage_records(window_start: datetime) -> list[dict]:
    usage_dir = _qwen_home() / "usage"
    if not usage_dir.exists():
        return []

    now = datetime.now(timezone.utc)
    months_needed = set()
    cursor = window_start
    while cursor <= now:
        months_needed.add(cursor.strftime("%Y-%m"))
        cursor = cursor.replace(day=28) + timedelta(days=4)
        if cursor.strftime("%Y-%m") != cursor.strftime("%Y-%m"):
            months_needed.add(cursor.strftime("%Y-%m"))

    records = []
    for month_str in sorted(months_needed):
        path = usage_dir / f"token-usage-{month_str}.jsonl"
        if not path.exists():
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        rec_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                    if rec_time >= window_start:
                        records.append(rec)
        except OSError:
            continue

    return records


def _aggregate_usage(records: list[dict], pricing: dict[str, dict[str, float]]) -> dict:
    by_model: dict[str, dict] = {}
    total_input = 0
    total_output = 0
    total_cached = 0
    total_thoughts = 0
    total_effective = 0
    total_cost_usd = 0.0
    request_count = 0

    for rec in records:
        model = rec.get("model", "unknown")
        inp = rec.get("inputTokens", 0) or 0
        out = rec.get("outputTokens", 0) or 0
        cached = rec.get("cachedTokens", 0) or 0
        thoughts = rec.get("thoughtsTokens", 0) or 0
        total = rec.get("totalTokens", 0) or 0
        effective = total - cached

        # Cost: uncached input at input rate, output+thoughts at output rate.
        # Cached tokens are billed at ~10% of input rate (cache hit discount).
        model_rates = pricing.get(model)
        if model_rates:
            uncached_input = max(0, inp - cached)
            cost = (
                uncached_input * model_rates["in"]
                + cached * model_rates["in"] * 0.1
                + (out + thoughts) * model_rates["out"]
            ) / 1_000_000
        else:
            cost = 0.0

        if model not in by_model:
            by_model[model] = {
                "requests": 0,
                "input": 0,
                "output": 0,
                "cached": 0,
                "thoughts": 0,
                "total": 0,
                "effective": 0,
                "cost_usd": 0.0,
            }
        by_model[model]["requests"] += 1
        by_model[model]["input"] += inp
        by_model[model]["output"] += out
        by_model[model]["cached"] += cached
        by_model[model]["thoughts"] += thoughts
        by_model[model]["total"] += total
        by_model[model]["effective"] += effective
        by_model[model]["cost_usd"] += cost

        total_input += inp
        total_output += out
        total_cached += cached
        total_thoughts += thoughts
        total_effective += effective
        total_cost_usd += cost
        request_count += 1

    return {
        "by_model": by_model,
        "total_input": total_input,
        "total_output": total_output,
        "total_cached": total_cached,
        "total_thoughts": total_thoughts,
        "total_effective": total_effective,
        "total_cost_usd": total_cost_usd,
        "request_count": request_count,
    }


def _check_reachability(api_key: str, base_url: str) -> tuple[bool, list[str]]:
    url = f"{base_url}/models"
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        models = data.get("data", [])
        model_ids = [m.get("id", "") for m in models if isinstance(m, dict)]
        return True, model_ids
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, ["API key rejected (401)"]
        return False, [f"HTTP {e.code}"]
    except Exception as exc:
        return False, [str(exc)]


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def check():
    label = "Qwen Token Plan"
    provider_id = "qwen-token-plan"

    def result(status, message=None, source="local", **extra):
        out = {"id": provider_id, "label": label, "status": status, "source": source}
        if message:
            out["message"] = message
        out.update(extra)
        return out

    api_key = _read_api_key()
    if not api_key:
        return result("unavailable", "No Qwen API key found in ~/.qwen/settings.json")

    base_url = _read_base_url()
    credit_limit = _env_int("USAGE_MONITOR_QWEN_CREDIT_LIMIT", DEFAULT_CREDIT_LIMIT)
    window_days = _env_int("USAGE_MONITOR_QWEN_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)
    usd_per_credit = _env_float("USAGE_MONITOR_QWEN_USD_PER_CREDIT")
    pricing = _load_pricing()

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)
    window_end_estimated = window_start + timedelta(days=window_days)

    records = _load_usage_records(window_start)
    usage = _aggregate_usage(records, pricing)

    reachable, model_ids = _check_reachability(api_key, base_url)

    details = []
    tier_name = _PLAN_TIERS.get(credit_limit, "Custom")
    details.append(f"Plan: {tier_name} ({credit_limit:,} credits / {window_days}d)")

    if usage["by_model"]:
        model_parts = []
        for model, stats in sorted(usage["by_model"].items()):
            cost_str = f"${stats['cost_usd']:.2f}" if stats["cost_usd"] > 0 else "?"
            model_parts.append(
                f"{model}: {stats['requests']} req, "
                f"{_format_tokens(stats['effective'])} tok ({cost_str})"
            )
        details.append("Models: " + " | ".join(model_parts[:5]))
        if len(model_parts) > 5:
            details.append(f"  +{len(model_parts) - 5} more")

    if not reachable:
        return result(
            "error",
            f"Qwen API unreachable: {', '.join(model_ids)}",
            source="api",
            details=details,
        )

    if usage["request_count"] == 0:
        return result(
            "ok",
            f"API reachable ({len(model_ids)} models), no usage in last {window_days}d",
            source="api",
            details=details + [f"{len(model_ids)} models available"],
        )

    # Build usage window
    effective = usage["total_effective"]
    total_all = sum(s["total"] for s in usage["by_model"].values())
    cost_usd = usage["total_cost_usd"]

    if usd_per_credit is not None and usd_per_credit > 0:
        credits_used = cost_usd / usd_per_credit
        used_pct = round(100.0 * credits_used / credit_limit, 1) if credit_limit > 0 else 0
        remaining_pct = round(max(0, 100.0 - used_pct), 1)

        window = {
            "label": f"{window_days}-day window ({tier_name})",
            "used_percent": min(used_pct, 100.0),
            "remaining_percent": remaining_pct,
            "reset_at": window_end_estimated.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "detail": f"{credits_used:.0f}/{credit_limit} credits (${cost_usd:.2f} est. cost)",
        }

        details.insert(1, f"Credits: {credits_used:.0f} / {credit_limit} ({used_pct:.0f}%)")
        details.append(f"Rate: ${usd_per_credit:.4f}/credit | Est. cost: ${cost_usd:.2f}")

        exhausted = used_pct >= 100
        warning = used_pct >= 85 and not exhausted
        status = "quota_exhausted" if exhausted else ("warning" if warning else "ok")
    else:
        # No conversion rate — show USD cost estimate only
        window = {
            "label": f"{window_days}-day window ({tier_name})",
            "used_percent": None,
            "remaining_percent": None,
            "reset_at": window_end_estimated.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "detail": f"${cost_usd:.2f} est. cost | {_format_tokens(effective)} eff. / {_format_tokens(total_all)} total tokens",
        }
        details.append(f"Set USAGE_MONITOR_QWEN_USD_PER_CREDIT to enable credit tracking")
        status = "ok"

    return result(
        status,
        source="local",
        windows=[window],
        details=details,
    )

# SPDX-License-Identifier: MIT
"""Built-in adapters with monkeypatched fake HTTP payloads.

_http_get_json is replaced with fakes, so no real network or credentials
are involved; tokens below are literal placeholders.
"""
from __future__ import annotations

import json

import pytest

from usage_monitor_app import core
from usage_monitor_app.core import Money


def _conf(provider_type, **extra):
    base = {
        "id": "t",
        "label": "Test",
        "type": provider_type,
        "credential": {"source": "literal", "value": "fake-token"},
    }
    base.update(extra)
    return base


class _FakeHttp:
    """Controllable fake for core._http_get_json.

    Map URL-suffix -> (status_code, body); unmatched URLs return (404, {}).
    `.calls` records every requested URL.
    """

    def __init__(self):
        self.routes: dict[str, tuple[int, object]] = {}
        self.calls: list[str] = []

    def __setitem__(self, suffix, resp):
        self.routes[suffix] = resp

    def __call__(self, url, token=None, *, timeout=12.0, headers=None):
        self.calls.append(url)
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return 404, {}


@pytest.fixture()
def fake_http(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(core, "_http_get_json", fake)
    return fake


def test_codex_wham_usage(monkeypatch, tmp_path):
    auth = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "at-test",
            "refresh_token": "rt-test",
            "account_id": "acc-1",
        },
    }
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    def fake_get(url, token=None, *, timeout=12.0, headers=None):
        assert "wham/usage" in url
        assert token == "at-test"
        assert headers and headers.get("ChatGPT-Account-Id") == "acc-1"
        return 200, {
            "plan_type": "prolite",
            "rate_limit": {
                "primary_window": {"used_percent": 3.0, "reset_at": "2026-08-08T00:00:00Z"},
                "secondary_window": {"used_percent": 12.5, "reset_at": "2026-08-14T00:00:00Z"},
            },
            "rate_limit_reset_credits": {"available_count": 1},
        }

    monkeypatch.setattr(core, "_http_get_json", fake_get)
    conf = {
        "id": "codex",
        "name": "Codex Plus",
        "type": "codex",
        "auth_path": str(auth_path),
    }
    # Prevent JWT refresh path from thinking token is expired
    monkeypatch.setattr("usage_monitor_app.codex._needs_refresh", lambda tokens: False)
    ps = core._adapter_codex(conf)
    assert ps.status == "ok"
    assert ps.id == "codex"
    assert ps.label == "Codex Plus"
    assert [w.label for w in ps.windows] == ["Current session", "Current week"]
    assert ps.windows[0].used_percent == 3.0
    assert ps.windows[0].remaining_percent == 97.0
    assert any("plan: Prolite" in d or "plan: prolite" in d.lower() or "Prolite" in d or "prolite" in d for d in ps.details)
    assert any("banked resets" in d for d in ps.details)


def test_codex_missing_session(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_MONITOR_CODEX_AUTH_FILE", str(tmp_path / "missing.json"))
    # Avoid real keychain / home auth
    monkeypatch.setattr("usage_monitor_app.codex._keychain_get", lambda **k: None)
    monkeypatch.setattr("usage_monitor_app.codex._auth_paths", lambda conf: [tmp_path / "nope.json"])
    ps = core._adapter_codex({"id": "codex", "name": "Codex", "type": "codex"})
    assert ps.status == "unavailable"
    assert "codex-login" in (ps.message or "")


def test_deepseek_happy_path(fake_http):
    fake_http["/user/balance"] = (200, {
        "is_available": True,
        "balance_infos": [
            {"currency": "cny", "total_balance": "48.50", "granted_balance": "8.50", "topped_up_balance": "40.00"}
        ],
    })
    ps = core._adapter_deepseek(_conf("deepseek"))
    assert ps.status == "ok"
    assert ps.balance == Money(48.5, "CNY")
    assert any("granted" in d for d in ps.details)


def test_deepseek_unavailable_balance(fake_http):
    fake_http["/user/balance"] = (200, {"is_available": False, "balance_infos": []})
    ps = core._adapter_deepseek(_conf("deepseek"))
    assert ps.status == "quota_exhausted"


def test_deepseek_402(fake_http):
    fake_http["/user/balance"] = (402, {"error": "no balance"})
    ps = core._adapter_deepseek(_conf("deepseek"))
    assert ps.status == "quota_exhausted"


def test_kimi_happy_path(fake_http):
    fake_http["/users/me/balance"] = (200, {
        "code": 0,
        "data": {"available_balance": 88.0, "voucher_balance": 8.0, "cash_balance": 80.0, "currency": "usd"},
    })
    ps = core._adapter_kimi(_conf("kimi"))
    assert ps.status == "ok"
    assert ps.balance == Money(88.0, "USD")
    assert any("voucher" in d for d in ps.details)


def test_kimi_unrecognized_shape(fake_http):
    fake_http["/users/me/balance"] = (200, {"unexpected": True})
    ps = core._adapter_kimi(_conf("kimi"))
    assert ps.status == "ok"
    assert ps.balance is None
    assert any("unrecognized" in d for d in ps.details)


def test_openai_compatible_balance(fake_http):
    fake_http["/credits"] = (200, {"balance": 3.75, "currency": "eur"})
    conf = _conf("openai-compatible", base_url="https://example.invalid/v1", credits_endpoint="/credits")
    ps = core._adapter_openai_compatible(conf)
    assert ps.status == "ok"
    assert ps.balance == Money(3.75, "EUR")


def test_supergrok_weekly_pool(fake_http, tmp_path, monkeypatch):
    auth = {
        "https://auth.x.ai::client": {
            "key": "sess-token",
            "user_id": "user-123",
            "expires_at": "2099-01-01T00:00:00Z",
            "auth_mode": "oidc",
        }
    }
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")
    monkeypatch.setenv("USAGE_MONITOR_GROK_AUTH_FILE", str(auth_path))

    fake_http["/billing?format=credits"] = (200, {
        "config": {
            "creditUsagePercent": 42.5,
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "2026-08-07T00:00:00Z",
                "end": "2026-08-14T00:00:00Z",
            },
            "isUnifiedBillingUser": True,
            "prepaidBalance": {"val": 500},
            "productUsage": [
                {"product": "GrokBuild", "usagePercent": 30.0},
                {"product": "GrokImagine", "usagePercent": 12.5},
            ],
        }
    })
    ps = core._adapter_supergrok({"id": "supergrok", "label": "SuperGrok", "type": "supergrok"})
    assert ps.status == "ok"
    assert len(ps.windows) == 1
    assert ps.windows[0].label == "Current week"
    assert ps.windows[0].used_percent == 42.5
    assert ps.windows[0].remaining_percent == 57.5
    assert ps.windows[0].reset_at == "2026-08-14T00:00:00Z"
    assert ps.balance == Money(5.0, "USD")
    assert any("GrokBuild" in d for d in ps.details)
    assert any("unified weekly pool" in d for d in ps.details)
    assert any(c.endswith("/billing?format=credits") for c in fake_http.calls)


def test_supergrok_exhausted(fake_http, tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "e": {"key": "t", "user_id": "u", "expires_at": "2099-01-01T00:00:00Z"}
    }), encoding="utf-8")
    monkeypatch.setenv("USAGE_MONITOR_GROK_AUTH_FILE", str(auth_path))
    fake_http["/billing?format=credits"] = (200, {
        "config": {"creditUsagePercent": 100.0, "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "end": "2026-08-14T00:00:00Z"}}
    })
    ps = core._adapter_supergrok({"id": "supergrok", "type": "supergrok"})
    assert ps.status == "quota_exhausted"


def test_supergrok_missing_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_MONITOR_GROK_AUTH_FILE", str(tmp_path / "missing.json"))
    # Ensure home default is not used accidentally by pointing at missing path only.
    ps = core._adapter_supergrok({"id": "supergrok", "type": "supergrok", "auth_path": str(tmp_path / "nope.json")})
    assert ps.status == "unavailable"
    assert "grok login" in (ps.message or "").lower() or "auth" in (ps.message or "").lower()


def test_xai_happy_path_auto_team(fake_http):
    team = "65c1e471-205f-4566-9c5a-07198bcdf4ce"
    fake_http["/auth/management-keys/validation"] = (200, {
        "apiKeyId": "key-1",
        "teamId": team,
        "scope": "SCOPE_TEAM",
        "scopeId": team,
    })
    fake_http[f"/v1/billing/teams/{team}/prepaid/balance"] = (200, {
        "total": {"val": "-4500"},
        "changes": [
            {"changeOrigin": "PURCHASE", "amount": {"val": "-5000"}},
            {"changeOrigin": "SPEND", "amount": {"val": "500"}},
        ],
    })
    fake_http[f"/v1/billing/teams/{team}/postpaid/invoice/preview"] = (200, {
        "coreInvoice": {
            "prepaidCreditsUsed": {"val": "500"},
            "amountAfterVat": "0",
        },
        "effectiveSpendingLimit": "0",
        "billingCycle": {"year": 2026, "month": 8},
    })
    ps = core._adapter_xai(_conf("xai"))
    assert ps.status == "ok"
    assert ps.balance == Money(45.0, "USD")
    assert ps.usage == Money(5.0, "USD")
    assert any("team:" in d for d in ps.details)
    assert any("purchased" in d for d in ps.details)
    assert any("billing cycle: 2026-08" in d for d in ps.details)


def test_xai_explicit_team_id_skips_validation(fake_http):
    team = "team-explicit"
    fake_http[f"/v1/billing/teams/{team}/prepaid/balance"] = (200, {
        "total": {"val": "-1000"},
        "changes": [],
    })
    conf = _conf("grok", team_id=team, include_invoice_preview=False)
    ps = core._adapter_xai(conf)
    assert ps.status == "ok"
    assert ps.balance == Money(10.0, "USD")
    assert not any("validation" in c for c in fake_http.calls)
    assert any(c.endswith(f"/v1/billing/teams/{team}/prepaid/balance") for c in fake_http.calls)


def test_xai_depleted_prepaid(fake_http):
    team = "team-zero"
    fake_http[f"/v1/billing/teams/{team}/prepaid/balance"] = (200, {
        "total": {"val": "0"},
        "changes": [{"changeOrigin": "SPEND", "amount": {"val": "2500"}}],
    })
    conf = _conf("xai", team_id=team, include_invoice_preview=False)
    ps = core._adapter_xai(conf)
    assert ps.status == "quota_exhausted"
    assert ps.balance == Money(0.0, "USD")
    assert ps.usage == Money(25.0, "USD")


def test_xai_missing_credential():
    conf = {"id": "xai", "label": "Grok", "type": "xai", "credential": {"source": "literal", "value": ""}}
    ps = core._adapter_xai(conf)
    assert ps.status == "unavailable"
    assert "management key" in (ps.message or "").lower()


def test_xai_warn_below(fake_http):
    team = "team-low"
    fake_http[f"/v1/billing/teams/{team}/prepaid/balance"] = (200, {
        "total": {"val": "-50"},  # $0.50 remaining
        "changes": [],
    })
    conf = _conf("xai", team_id=team, include_invoice_preview=False, warn_below_usd=1.0)
    ps = core._adapter_xai(conf)
    assert ps.status == "warning"
    assert ps.balance == Money(0.5, "USD")


def test_openai_credit_grants_session_key_required(fake_http):
    fake_http["/dashboard/billing/credit_grants"] = (403, {
        "error": {
            "message": (
                "Your request to GET /v1/dashboard/billing/credit_grants must be made "
                "with a session key (that is, it can only be made from the browser). "
                "You made it with the following key type: secret."
            ),
            "type": "server_error",
        }
    })
    conf = _conf(
        "openai-compatible",
        base_url="https://api.openai.com",
        credits_endpoint="/dashboard/billing/credit_grants",
    )
    ps = core._adapter_openai_compatible(conf)
    assert ps.status == "unavailable"
    assert "session key" in (ps.message or "").lower()
    assert "Admin key" in (ps.message or "")


def test_openai_org_costs_happy_path(monkeypatch):
    calls = []

    def fake_get(url, token=None, *, timeout=12.0, headers=None):
        calls.append((url, token, headers))
        assert "/organization/costs" in url
        assert "start_time=" in url
        return 200, {
            "object": "page",
            "data": [
                {
                    "object": "bucket",
                    "results": [
                        {"object": "organization.costs.result", "amount": {"value": 1.25, "currency": "usd"}},
                        {"object": "organization.costs.result", "amount": {"value": 0.75, "currency": "usd"}},
                    ],
                }
            ],
        }

    monkeypatch.setattr(core, "_http_get_json", fake_get)
    ps = core._adapter_openai(_conf("openai", organization="org-test"))
    assert ps.status == "ok"
    assert ps.usage == Money(2.0, "USD")
    assert any("last" in d and "d" in d for d in ps.details)
    assert any("remaining balance not exposed" in d for d in ps.details)
    assert calls[0][2].get("OpenAI-Organization") == "org-test"


def test_openai_org_costs_rejects_project_key(monkeypatch):
    monkeypatch.setattr(
        core,
        "_http_get_json",
        lambda *a, **k: (403, {"error": {"message": "You have insufficient permissions for this operation."}}),
    )
    ps = core._adapter_openai(_conf("openai"))
    assert ps.status == "unavailable"
    assert "Admin key" in (ps.message or "")


def test_openai_compatible_models_fallback(fake_http):
    fake_http["/models"] = (200, {"data": [{"id": "m1"}, {"id": "m2"}]})
    conf = _conf("openai-compatible", base_url="https://example.invalid/v1")
    ps = core._adapter_openai_compatible(conf)
    assert ps.status == "ok"
    assert ps.balance is None
    assert any("models: 2" in d for d in ps.details)


def test_openai_compatible_requires_base_url(fake_http):
    ps = core._adapter_openai_compatible(_conf("openai-compatible"))
    assert ps.status == "unavailable"
    assert "base_url" in (ps.message or "")
    assert fake_http.calls == []


def test_openai_compatible_429(fake_http):
    fake_http["/models"] = (429, {"error": "rate limited"})
    conf = _conf("openai-compatible", base_url="https://example.invalid/v1")
    ps = core._adapter_openai_compatible(conf)
    assert ps.status == "rate_limited"


def test_adapter_http_exception_becomes_unavailable(monkeypatch):
    def raising(url, token=None, *, timeout=12.0, headers=None):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(core, "_http_get_json", raising)
    ps = core._adapter_deepseek(_conf("deepseek"))
    assert ps.status == "unavailable"
    assert "no route to host" in (ps.message or "")


def test_placeholder_adapter_details():
    ps = core._adapter_placeholder(_conf("placeholder", base_url="https://example.invalid"))
    assert ps.status == "unknown"
    assert ps.details == ["base_url: https://example.invalid"]


def test_account_usage_window_labels_aliased(monkeypatch):
    """Hermes account_usage labels (Session/Weekly) render like Claude/Kimi."""
    import sys
    import types
    from types import SimpleNamespace

    snap = SimpleNamespace(
        unavailable_reason=None,
        source="usage_api",
        details=(),
        windows=[
            SimpleNamespace(label="Session", used_percent=20.0, reset_at=None, detail=None),
            SimpleNamespace(label="Weekly", used_percent=10.0, reset_at=None, detail=None),
        ],
    )
    mod = types.ModuleType("agent.account_usage")
    mod.fetch_account_usage = lambda provider: snap
    pkg = types.ModuleType("agent")
    pkg.account_usage = mod
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.account_usage", mod)

    ps = core._from_account_usage("openai-codex", "Codex")
    assert ps.status == "ok"
    assert [w.label for w in ps.windows] == ["Current session", "Current week"]
    assert ps.windows[0].remaining_percent == 80.0

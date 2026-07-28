"""Built-in adapters with monkeypatched fake HTTP payloads.

_http_get_json is replaced with fakes, so no real network or credentials
are involved; tokens below are literal placeholders.
"""
from __future__ import annotations

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

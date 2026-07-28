"""Coercion of adapter return values into ProviderStatus / UsageWindow."""
from __future__ import annotations

import pytest

from usage_monitor_app.core import Money, ProviderStatus, UsageWindow, _coerce_provider, _coerce_window


def test_coerce_window_passthrough():
    w = UsageWindow(label="daily", used_percent=10.0)
    assert _coerce_window(w) is w


def test_coerce_window_from_dict():
    w = _coerce_window({
        "name": "weekly",
        "used_percent": "42.5",
        "remaining_percent": 57.5,
        "reset_at": "2026-08-01T00:00:00Z",
        "detail": "resets monthly",
    })
    assert w.label == "weekly"
    assert w.used_percent == 42.5
    assert w.remaining_percent == 57.5
    assert w.reset_at == "2026-08-01T00:00:00Z"
    assert w.detail == "resets monthly"


def test_coerce_window_defaults_and_bad_numbers():
    w = _coerce_window({"used_percent": "not-a-number", "remaining_percent": True})
    assert w.label == "window"
    assert w.used_percent is None
    assert w.remaining_percent is None  # bools are rejected by _safe_float
    assert w.reset_at is None


def test_coerce_window_rejects_unknown_type():
    with pytest.raises(TypeError):
        _coerce_window(42)


def test_coerce_provider_passthrough():
    p = ProviderStatus(id="x", label="X")
    assert _coerce_provider(p) is p


def test_coerce_provider_from_full_dict():
    p = _coerce_provider({
        "id": "ext1",
        "label": "External One",
        "status": "ok",
        "source": "external",
        "balance": {"amount": "12.5", "currency": "usd"},
        "usage": {"amount": 3.25},
        "windows": [{"label": "daily", "used_percent": 10}],
        "details": ["a", 1],
        "message": "fine",
    })
    assert p.id == "ext1"
    assert p.balance == Money(12.5, "usd")
    assert p.usage == Money(3.25, "USD")  # currency defaults to USD
    assert p.windows == [UsageWindow(label="daily", used_percent=10.0)]
    assert p.details == ["a", "1"]
    assert p.message == "fine"


def test_coerce_provider_from_minimal_dict():
    p = _coerce_provider({"name": "bare"})
    assert p.id == "bare"
    assert p.label == "External"  # label falls back to id, then "External"
    assert p.status == "unknown"
    assert p.balance is None
    assert p.usage is None
    assert p.windows == []
    assert p.details == []
    assert p.message is None


def test_coerce_provider_ignores_malformed_money_and_windows():
    p = _coerce_provider({
        "id": "x",
        "balance": {"currency": "USD"},  # no amount
        "usage": "nope",
        "windows": "not-a-list",
        "details": "not-a-list",
    })
    assert p.balance is None
    assert p.usage is None
    assert p.windows == []
    assert p.details == []


def test_coerce_provider_rejects_unknown_type():
    with pytest.raises(TypeError):
        _coerce_provider([("id", "x")])

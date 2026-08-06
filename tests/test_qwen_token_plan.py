# SPDX-License-Identifier: MIT
"""Tests for the Qwen Token Plan external adapter.

Uses monkeypatched filesystem and HTTP to avoid real network or credentials.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def qwen_adapter(tmp_path, monkeypatch):
    """Import the adapter with QWEN_HOME pointed at a temp directory."""
    qwen_home = tmp_path / ".qwen"
    qwen_home.mkdir()
    monkeypatch.setenv("USAGE_MONITOR_QWEN_HOME", str(qwen_home))
    monkeypatch.delenv("USAGE_MONITOR_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("USAGE_MONITOR_QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("USAGE_MONITOR_QWEN_CREDITS_PER_M_TOKEN", raising=False)
    monkeypatch.delenv("USAGE_MONITOR_QWEN_CREDIT_LIMIT", raising=False)
    monkeypatch.delenv("USAGE_MONITOR_QWEN_WINDOW_DAYS", raising=False)

    adapter_dir = Path(__file__).resolve().parents[1] / "plugin" / "adapters"
    sys.path.insert(0, str(adapter_dir))
    try:
        import qwen_token_plan
        import importlib
        importlib.reload(qwen_token_plan)
        yield qwen_token_plan, qwen_home
    finally:
        sys.path.remove(str(adapter_dir))
        sys.modules.pop("qwen_token_plan", None)


def _write_settings(qwen_home: Path, api_key: str = "sk-sp-fake-key", base_url: str | None = None):
    settings = {
        "env": {
            "BAILIAN_TOKEN_PLAN_API_KEY": api_key,
        },
        "modelProviders": {
            "openai": [
                {
                    "id": "qwen3.7-plus",
                    "envKey": "BAILIAN_TOKEN_PLAN_API_KEY",
                    "baseUrl": base_url or "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
                }
            ]
        },
    }
    (qwen_home / "settings.json").write_text(json.dumps(settings))


def _write_usage(qwen_home: Path, records: list[dict], month: str = "2026-08"):
    usage_dir = qwen_home / "usage"
    usage_dir.mkdir(exist_ok=True)
    path = usage_dir / f"token-usage-{month}.jsonl"
    with open(path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _fake_models_response():
    return json.dumps({
        "data": [
            {"id": "qwen3.7-plus", "owned_by": "system"},
            {"id": "qwen3.6-flash", "owned_by": "system"},
        ]
    }).encode()


class TestNoConfig:
    def test_no_settings_file(self, qwen_adapter):
        adapter, qwen_home = qwen_adapter
        result = adapter.check()
        assert result["status"] == "unavailable"
        assert "No Qwen API key" in result.get("message", "")

    def test_settings_without_api_key(self, qwen_adapter):
        adapter, qwen_home = qwen_adapter
        (qwen_home / "settings.json").write_text(json.dumps({"env": {}}))
        result = adapter.check()
        assert result["status"] == "unavailable"


class TestApiReachability:
    def test_api_unreachable(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)

        def fake_urlopen(req, timeout=10):
            raise ConnectionError("no route to host")

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert result["status"] == "error"
        assert "unreachable" in result.get("message", "")

    def test_api_key_rejected(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)

        import urllib.error
        def fake_urlopen(req, timeout=10):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert result["status"] == "error"
        assert "401" in result.get("message", "")


class TestUsageAggregation:
    def test_no_usage_data(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert result["status"] == "ok"
        assert "no usage" in result.get("message", "").lower()

    def test_usage_data_shown(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 1000,
                "outputTokens": 200,
                "cachedTokens": 500,
                "thoughtsTokens": 50,
                "totalTokens": 1250,
            },
            {
                "timestamp": "2026-08-06T11:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 2000,
                "outputTokens": 300,
                "cachedTokens": 1000,
                "thoughtsTokens": 100,
                "totalTokens": 2400,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert result["status"] == "ok"
        assert len(result["windows"]) == 1
        w = result["windows"][0]
        assert "7-day" in w["label"]
        assert w["used_percent"] is None
        assert "eff." in w["detail"]

    def test_old_records_excluded(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-07-01T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 999999,
                "outputTokens": 0,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 999999,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert "no usage" in result.get("message", "").lower()


class TestCreditConversion:
    def test_credits_calculated_when_rate_set(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        monkeypatch.setenv("USAGE_MONITOR_QWEN_USD_PER_CREDIT", "0.0024")
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 500000,
                "outputTokens": 10000,
                "cachedTokens": 400000,
                "thoughtsTokens": 100,
                "totalTokens": 510100,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)

        import importlib
        importlib.reload(adapter)
        result = adapter.check()
        assert result["status"] == "ok"
        w = result["windows"][0]
        assert w["used_percent"] is not None
        assert w["used_percent"] > 0
        assert "credits" in w["detail"]

    def test_quota_exhausted_at_100_percent(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        monkeypatch.setenv("USAGE_MONITOR_QWEN_USD_PER_CREDIT", "0.001")
        monkeypatch.setenv("USAGE_MONITOR_QWEN_CREDIT_LIMIT", "1")
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.8-max",
                "inputTokens": 100000,
                "outputTokens": 50000,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 150000,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)

        import importlib
        importlib.reload(adapter)
        result = adapter.check()
        assert result["status"] == "quota_exhausted"
        assert result["windows"][0]["used_percent"] >= 100

    def test_warning_at_85_percent(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        # qwen3.7-plus: 400K input * $0.50/M = $0.20 cost
        # $0.20 / $0.001/credit = 200 credits out of 250 = 80% → ok
        # 450K input * $0.50/M = $0.225 → 225/250 = 90% → warning
        monkeypatch.setenv("USAGE_MONITOR_QWEN_USD_PER_CREDIT", "0.001")
        monkeypatch.setenv("USAGE_MONITOR_QWEN_CREDIT_LIMIT", "250")
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 450000,
                "outputTokens": 0,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 450000,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)

        import importlib
        importlib.reload(adapter)
        result = adapter.check()
        assert result["status"] == "warning"
        assert 85 <= result["windows"][0]["used_percent"] < 100

    def test_expensive_model_costs_more(self, qwen_adapter, monkeypatch):
        """qwen3.8-max should cost ~4x more than qwen3.7-plus for same tokens."""
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)

        # 1M input tokens with qwen3.7-plus: $0.50
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 1000000,
                "outputTokens": 0,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 1000000,
            },
        ])
        import importlib
        importlib.reload(adapter)
        result_plus = adapter.check()
        cost_plus = result_plus["details"][1]  # "Models: ..." line

        # 1M input tokens with qwen3.8-max: $2.00
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.8-max",
                "inputTokens": 1000000,
                "outputTokens": 0,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 1000000,
            },
        ])
        importlib.reload(adapter)
        result_max = adapter.check()
        cost_max = result_max["details"][1]

        assert "$0.50" in cost_plus
        assert "$2.00" in cost_max


class TestEnvOverrides:
    def test_api_key_from_env(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        monkeypatch.setenv("USAGE_MONITOR_QWEN_API_KEY", "sk-sp-env-override")

        import io
        def fake_urlopen(req, timeout=10):
            assert req.get_header("Authorization") == "Bearer sk-sp-env-override"
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert result["status"] == "ok"

    def test_custom_window_days(self, qwen_adapter, monkeypatch):
        adapter, qwen_home = qwen_adapter
        _write_settings(qwen_home)
        monkeypatch.setenv("USAGE_MONITOR_QWEN_WINDOW_DAYS", "3")
        _write_usage(qwen_home, [
            {
                "timestamp": "2026-08-06T10:00:00Z",
                "model": "qwen3.7-plus",
                "inputTokens": 1000,
                "outputTokens": 100,
                "cachedTokens": 0,
                "thoughtsTokens": 0,
                "totalTokens": 1100,
            },
        ])

        import io
        def fake_urlopen(req, timeout=10):
            return io.BytesIO(_fake_models_response())

        monkeypatch.setattr(adapter.urllib.request, "urlopen", fake_urlopen)
        result = adapter.check()
        assert "3-day" in result["windows"][0]["label"]

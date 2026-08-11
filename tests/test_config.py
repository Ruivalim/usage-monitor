# SPDX-License-Identifier: MIT
"""Config loading from YAML and JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from usage_monitor_app import core
from usage_monitor_app import config as app_config

EXAMPLE_PROVIDERS = Path(__file__).resolve().parents[1] / "examples" / "providers.yaml"


def test_load_yaml_config(providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: a\n"
        "    type: placeholder\n"
        "defaults:\n"
        "  timeout: 3.0\n"
    )
    data = core.load_provider_config(path)
    assert data["providers"] == [{"id": "a", "type": "placeholder"}]
    assert data["defaults"] == {"timeout": 3.0}


def test_load_json_config(providers_file):
    payload = {"providers": [{"id": "b", "type": "placeholder"}], "defaults": {"timeout": 1.5}}
    path = providers_file(json.dumps(payload), name="providers.json")
    data = core.load_provider_config(path)
    assert data == payload


def test_missing_file_returns_defaults(tmp_path):
    data = core.load_provider_config(tmp_path / "nope.yaml")
    assert data["providers"] == []
    assert data["defaults"]["timeout"] == 12.0


def test_optional_hermes_agent_path_is_added_for_launchagent_env(tmp_path, monkeypatch):
    hermes_src = tmp_path / "hermes-agent"
    (hermes_src / "agent").mkdir(parents=True)
    monkeypatch.setenv("HERMES_PYTHON_SRC_ROOT", str(hermes_src))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(hermes_src)])

    assert core.ensure_optional_hermes_agent_path() == hermes_src
    assert sys.path[0] == str(hermes_src)


def test_empty_file_returns_defaults(providers_file):
    path = providers_file("\n")
    data = core.load_provider_config(path)
    assert data["providers"] == []


def test_non_dict_yaml_returns_defaults(providers_file):
    path = providers_file("- just\n- a\n- list\n")
    data = core.load_provider_config(path)
    assert data["providers"] == []


def test_yaml_without_providers_key_returns_defaults(providers_file):
    path = providers_file("defaults:\n  timeout: 5.0\n")
    data = core.load_provider_config(path)
    assert data["defaults"] == {"timeout": 12.0}
    assert data["providers"] == []


def test_invalid_json_raises(providers_file):
    path = providers_file("{not json", name="bad.json")
    with pytest.raises(Exception):
        core.load_provider_config(path)


def test_load_prices_tolerates_errors(tmp_path, providers_file):
    assert core.load_prices(tmp_path / "missing.yaml") == {}
    bad = providers_file("{not json", name="prices.json")
    assert core.load_prices(bad) == {}
    ok = providers_file(json.dumps({"openrouter": {"input": 1.0}}), name="prices.json")
    assert core.load_prices(ok) == {"openrouter": {"input": 1.0}}


def test_example_providers_file_covers_every_registry_type():
    """Shipped example documents every canonical registry type (aliases optional)."""
    data = core.load_provider_config(EXAMPLE_PROVIDERS)
    types = {p["type"] for p in data["providers"]}
    # Aliases like `grok` → supergrok need not each have a dedicated example entry.
    aliases = {"grok", "openai-codex"}  # aliases of supergrok / codex
    assert set(core.REGISTRY) - aliases <= types
    ids = [p["id"] for p in data["providers"]]
    assert len(ids) == len(set(ids))


def test_provider_items_from_config_merges_defaults_and_skips_disabled():
    config = {
        "defaults": {"timeout": 7.0},
        "providers": [
            {"id": "on", "type": "placeholder"},
            {"id": "off", "type": "placeholder", "enabled": False},
            "not-a-dict",
        ],
    }
    items = core._provider_items_from_config(config)
    ids = [i["id"] for i in items]
    assert ids == ["on"]
    assert items[0]["timeout"] == 7.0


def test_app_config_loads_dashboard_language_and_intervals(tmp_path, monkeypatch):
    monkeypatch.delenv("USAGE_MONITOR_REFRESH_INTERVAL", raising=False)
    monkeypatch.delenv("USAGE_MONITOR_MENUBAR_INTERVAL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        "dashboard:\n"
        "  language: pt\n"
        "  theme: light\n"
        "intervals:\n"
        "  refresh_seconds: 42\n"
        "  menubar_seconds: 7\n",
        encoding="utf-8",
    )
    cfg = app_config.load_app_config(path)
    assert cfg.dashboard.language == "pt-BR"  # `pt` is an accepted spelling of it
    assert cfg.intervals.refresh_seconds == 42
    assert cfg.intervals.menubar_seconds == 7


def test_app_config_env_interval_override(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("intervals:\n  refresh_seconds: 42\n  menubar_seconds: 7\n", encoding="utf-8")
    monkeypatch.setenv("USAGE_MONITOR_REFRESH_INTERVAL", "900")
    monkeypatch.setenv("USAGE_MONITOR_MENUBAR_INTERVAL", "300")
    cfg = app_config.load_app_config(path)
    assert cfg.intervals.refresh_seconds == 900
    assert cfg.intervals.menubar_seconds == 300

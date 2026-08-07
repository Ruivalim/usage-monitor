# SPDX-License-Identifier: MIT
"""Registry dispatch via collect_status with placeholder providers."""
from __future__ import annotations

from usage_monitor_app import core


def test_registry_has_expected_types():
    expected = {
        "deepseek",
        "kimi",
        "openai-compatible",
        "generic-http",
        "openai",
        "xai",
        "supergrok",
        "grok",
        "placeholder",
        "hermes-account-usage",
        "anthropic-subscription",
        "hermes-nous",
        "hermes-state-db",
        "claude-cli",
        "kimi-cli",
        "qwen-token-plan",
        "antigravity",
        "codex",
        "openai-codex",
    }
    assert expected <= set(core.REGISTRY)
    assert "hermes-auth" not in core.REGISTRY
    for adapter in core.REGISTRY.values():
        assert callable(adapter)


def test_placeholder_dispatch_no_credential(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: p1\n"
        "    name: Placeholder One\n"
        "    type: placeholder\n"
        "    message: no endpoint\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert len(snap.providers) == 1
    p = snap.providers[0]
    assert p.id == "p1"
    assert p.label == "Placeholder One"
    assert p.status == "unavailable"
    assert p.message == "no endpoint"
    assert snap.overall == "unknown"


def test_placeholder_dispatch_with_literal_credential(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: p2\n"
        "    type: placeholder\n"
        "    credential:\n"
        "      source: literal\n"
        "      value: fake-token-for-tests\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    p = snap.providers[0]
    assert p.status == "unknown"
    assert p.source == "config"


def test_unknown_type_gets_unknown_status(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: mystery\n"
        "    type: does-not-exist\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    p = snap.providers[0]
    assert p.id == "mystery"
    assert p.status == "unknown"
    assert "Unknown provider type" in (p.message or "")


def test_disabled_providers_are_skipped(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: keep\n"
        "    type: placeholder\n"
        "  - id: skip\n"
        "    type: placeholder\n"
        "    enabled: false\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert [p.id for p in snap.providers] == ["keep"]


def test_yaml_only_no_auto_external_adapters(isolated_state, providers_file, tmp_path, monkeypatch):
    """Adapters on disk must not run unless listed in providers.yaml."""
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    (adapter_dir / "ghost.py").write_text(
        "def check():\n"
        "    return {'id': 'ghost', 'label': 'Ghost', 'status': 'ok', 'source': 'test'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "ADAPTER_DIR", adapter_dir)
    path = providers_file(
        "providers:\n"
        "  - id: only\n"
        "    name: Only YAML\n"
        "    type: placeholder\n"
        "    message: yaml-only\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert [p.id for p in snap.providers] == ["only"]
    assert all(p.id != "ghost" for p in snap.providers)


def test_multi_sub_same_type_uses_name(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: a\n"
        "    name: First\n"
        "    type: placeholder\n"
        "    message: one\n"
        "  - id: b\n"
        "    name: Second\n"
        "    type: placeholder\n"
        "    message: two\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert [(p.id, p.label) for p in snap.providers] == [("a", "First"), ("b", "Second")]


def test_duplicate_ids_keep_first(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: same\n"
        "    name: First\n"
        "    type: placeholder\n"
        "  - id: same\n"
        "    name: Second\n"
        "    type: placeholder\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert len(snap.providers) == 1
    assert snap.providers[0].label == "First"

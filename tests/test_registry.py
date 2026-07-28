"""Registry dispatch via collect_status with placeholder providers."""
from __future__ import annotations

from usage_monitor_app import core


def test_registry_has_expected_types():
    expected = {
        "deepseek",
        "kimi",
        "openai-compatible",
        "generic-http",
        "placeholder",
        "hermes-account-usage",
        "anthropic-subscription",
        "hermes-auth",
        "hermes-nous",
        "hermes-state-db",
    }
    assert expected <= set(core.REGISTRY)
    for adapter in core.REGISTRY.values():
        assert callable(adapter)


def test_placeholder_dispatch_no_credential(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: p1\n"
        "    label: Placeholder One\n"
        "    type: placeholder\n"
        "    message: no endpoint\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert len(snap.providers) == 1
    p = snap.providers[0]
    assert p.id == "p1"
    assert p.status == "unavailable"  # placeholder without token
    assert p.message == "no endpoint"
    assert snap.overall == "unknown"  # unavailable has severity 1


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
    assert p.status == "unknown"  # placeholder with token reports unknown
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
    # NB: YAML 1.1 would parse a bare `on`/`off` scalar as bool, so use
    # ids that survive safe_load unchanged.
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


def test_include_auth_false_filters_hermes_auth(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: plain\n"
        "    type: placeholder\n"
        "  - id: auth\n"
        "    type: hermes-auth\n"
    )
    snap = core.collect_status(persist=False, include_auth=False, providers_file=path)
    assert [p.id for p in snap.providers] == ["plain"]


def test_adapter_exception_becomes_error_status(isolated_state, providers_file, monkeypatch):
    def boom(conf):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(core.REGISTRY, "placeholder", boom)
    path = providers_file(
        "providers:\n"
        "  - id: bad\n"
        "    type: placeholder\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    p = snap.providers[0]
    assert p.id == "bad"
    assert p.status == "error"
    assert "kaboom" in (p.message or "")
    assert snap.overall == "error"
    assert any("bad" in e for e in snap.meta["errors"])


def test_severity_aggregation(isolated_state, providers_file):
    path = providers_file(
        "providers:\n"
        "  - id: warn\n"
        "    type: fake-warning\n"
        "  - id: fine\n"
        "    type: fake-ok\n"
    )
    from usage_monitor_app.core import ProviderStatus

    core.REGISTRY["fake-warning"] = lambda conf: ProviderStatus("warn", "Warn", status="warning", message="w")
    core.REGISTRY["fake-ok"] = lambda conf: ProviderStatus("fine", "Fine", status="ok")
    try:
        snap = core.collect_status(persist=False, providers_file=path)
    finally:
        del core.REGISTRY["fake-warning"]
        del core.REGISTRY["fake-ok"]
    assert snap.overall == "warning"
    assert snap.alerts == [{"level": "warning", "provider": "warn", "message": "w"}]

# SPDX-License-Identifier: MIT
"""Per-provider relevance overrides (overrides.json)."""
from __future__ import annotations

import json

from usage_monitor_app import core
from usage_monitor_app.core import ProviderStatus


def test_overrides_roundtrip(tmp_path):
    path = tmp_path / "overrides.json"
    assert core.load_overrides(path) == {}
    core.set_provider_relevant("nous", False, path)
    assert core.load_overrides(path) == {"nous": {"relevant": False}}
    # Setting back to True removes the entry (True is the default).
    core.set_provider_relevant("nous", True, path)
    assert core.load_overrides(path) == {}


def test_load_overrides_tolerates_garbage(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text("{not json", encoding="utf-8")
    assert core.load_overrides(path) == {}
    path.write_text('["a", "list"]', encoding="utf-8")
    assert core.load_overrides(path) == {}


def test_irrelevant_provider_excluded_from_overall_and_alerts(isolated_state, providers_file, monkeypatch):
    overrides = tmp_overrides(monkeypatch)
    core.REGISTRY["fake-exhausted"] = lambda conf: ProviderStatus("nous", "Nous", status="quota_exhausted", message="no credits")
    try:
        path = providers_file(
            "providers:\n"
            "  - id: nous\n"
            "    type: fake-exhausted\n"
        )
        snap = core.collect_status(persist=False, providers_file=path)
        assert snap.overall == "error"
        assert snap.alerts[0]["provider"] == "nous"

        core.set_provider_relevant("nous", False, overrides)
        snap = core.collect_status(persist=False, providers_file=path)
        assert snap.overall == "ok"
        assert snap.alerts == []
        assert snap.providers[0].relevant is False
        # Serialized payload keeps the flag so UIs can render the muted state.
        assert core._to_plain(snap)["providers"][0]["relevant"] is False
    finally:
        del core.REGISTRY["fake-exhausted"]


def tmp_overrides(monkeypatch):
    """Redirect OVERRIDES_FILE to a per-test tmp path; returns the path."""
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "overrides.json"
    monkeypatch.setattr(core, "OVERRIDES_FILE", path)
    return path


def test_collect_status_reads_overrides_file(isolated_state, providers_file, monkeypatch):
    overrides = tmp_overrides(monkeypatch)
    overrides.write_text(json.dumps({"demo": {"relevant": False}}), encoding="utf-8")
    path = providers_file(
        "providers:\n"
        "  - id: demo\n"
        "    type: placeholder\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert snap.providers[0].relevant is False

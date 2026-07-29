# SPDX-License-Identifier: MIT
"""Snapshot JSONL persistence and latest_snapshot."""
from __future__ import annotations

import json

from usage_monitor_app import core


def _run(isolated_state, providers_file, n=1):
    path = providers_file(
        "providers:\n"
        "  - id: demo\n"
        "    type: placeholder\n"
        "    message: hi\n"
    )
    snaps = [core.collect_status(persist=True, providers_file=path) for _ in range(n)]
    return snaps


def test_collect_status_appends_jsonl(isolated_state, providers_file):
    snaps = _run(isolated_state, providers_file, n=2)
    lines = isolated_state.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line, snap in zip(lines, snaps):
        rec = json.loads(line)
        assert rec["checked_at"] == snap.checked_at
        assert rec["overall"] == snap.overall
        assert rec["providers"][0]["id"] == "demo"
        assert "meta" in rec


def test_persist_false_writes_nothing(isolated_state, providers_file):
    path = providers_file("providers:\n  - id: demo\n    type: placeholder\n")
    core.collect_status(persist=False, providers_file=path)
    assert not isolated_state.exists()


def test_latest_snapshot_reads_back(isolated_state, providers_file):
    snaps = _run(isolated_state, providers_file, n=3)
    latest = core.latest_snapshot(2)
    assert len(latest) == 2
    assert [s["checked_at"] for s in latest] == [snaps[-2].checked_at, snaps[-1].checked_at]


def test_latest_snapshot_limit_floor(isolated_state, providers_file):
    _run(isolated_state, providers_file, n=2)
    assert len(core.latest_snapshot(0)) == 1  # max(1, limit)


def test_latest_snapshot_missing_file(isolated_state):
    assert core.latest_snapshot() == []


def test_latest_snapshot_skips_corrupt_lines(isolated_state):
    isolated_state.write_text(
        '{"overall": "ok"}\nnot json\n{"overall": "error"}\n',
        encoding="utf-8",
    )
    latest = core.latest_snapshot(10)
    assert [s["overall"] for s in latest] == ["ok", "error"]


def test_to_plain_drops_empty_fields():
    snap = core.MonitorSnapshot(
        checked_at="t",
        overall="ok",
        providers=[core.ProviderStatus(id="x", label="X", status="ok")],
        alerts=[],
    )
    plain = core._to_plain(snap)
    provider = plain["providers"][0]
    assert provider["id"] == "x"
    assert provider["label"] == "X"
    assert provider["status"] == "ok"
    assert "alerts" not in plain  # empty lists are dropped
    assert "balance" not in provider  # None fields are dropped
    assert "windows" not in provider
    assert "meta" not in plain  # empty dicts are dropped

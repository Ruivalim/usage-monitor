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


def test_latest_snapshot_reads_only_the_tail(isolated_state, monkeypatch):
    """A huge log must not be read end to end to answer for a few entries."""
    isolated_state.write_text(
        "".join(json.dumps({"overall": "ok", "n": i, "pad": "x" * 4096}) + "\n" for i in range(400)),
        encoding="utf-8",
    )
    real_open = type(isolated_state).open
    read_bytes = 0

    class CountingHandle:
        def __init__(self, handle):
            self._handle = handle

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def read(self, *args):
            nonlocal read_bytes
            data = self._handle.read(*args)
            read_bytes += len(data)
            return data

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

    monkeypatch.setattr(
        type(isolated_state),
        "open",
        lambda self, *a, **kw: CountingHandle(real_open(self, *a, **kw)),
    )
    latest = core.latest_snapshot(3)
    assert [s["n"] for s in latest] == [397, 398, 399]
    assert read_bytes < isolated_state.stat().st_size / 4


def test_snapshot_file_rotates_past_the_cap(isolated_state, providers_file, monkeypatch):
    path = providers_file("providers:\n  - id: demo\n    type: placeholder\n")
    monkeypatch.setenv("USAGE_MONITOR_SNAPSHOT_MAX_BYTES", "1000000")
    first = core.collect_status(persist=True, providers_file=path)
    assert isolated_state.exists()

    monkeypatch.setenv("USAGE_MONITOR_SNAPSHOT_MAX_BYTES", "1")  # next write blows the cap
    second = core.collect_status(persist=True, providers_file=path)
    backup = core.rotated_snapshot_file(isolated_state)
    # The snapshot is appended first, then the oversized file becomes the backup,
    # so nothing written is ever lost to a rotation.
    assert not isolated_state.exists()
    assert [json.loads(l)["checked_at"] for l in backup.read_text().splitlines()] == [
        first.checked_at,
        second.checked_at,
    ]

    third = core.collect_status(persist=True, providers_file=path)
    assert [json.loads(l)["checked_at"] for l in backup.read_text().splitlines()] == [third.checked_at]


def test_latest_snapshot_spans_a_rotation(isolated_state, monkeypatch):
    core.rotated_snapshot_file(isolated_state).write_text(
        '{"overall": "ok", "n": 1}\n{"overall": "ok", "n": 2}\n', encoding="utf-8"
    )
    isolated_state.write_text('{"overall": "ok", "n": 3}\n', encoding="utf-8")
    assert [s["n"] for s in core.latest_snapshot(3)] == [1, 2, 3]
    assert [s["n"] for s in core.latest_snapshot(1)] == [3]


def test_rotation_disabled_by_zero_cap(isolated_state, monkeypatch):
    monkeypatch.setenv("USAGE_MONITOR_SNAPSHOT_MAX_BYTES", "0")
    isolated_state.write_text("x" * 1024, encoding="utf-8")
    assert core.rotate_snapshots(isolated_state) is None
    assert isolated_state.exists()


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

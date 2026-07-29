# SPDX-License-Identifier: MIT
"""Pytest harness for usage_monitor_app.

All USAGE_MONITOR_* state is redirected to a temporary directory BEFORE
usage_monitor_app.core is imported, because core reads those env vars at
import time. Tests never touch the real ~/.hermes tree, never perform
network access, and never use real credentials (only literal fake tokens).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_HOME = Path(tempfile.mkdtemp(prefix="usage-monitor-test-home-"))

os.environ["USAGE_MONITOR_HOME"] = str(_HOME)
os.environ["USAGE_MONITOR_SNAPSHOT_FILE"] = str(_HOME / "snapshots.jsonl")
os.environ["USAGE_MONITOR_ADAPTER_DIR"] = str(_HOME / "adapters")
os.environ["USAGE_MONITOR_PROVIDERS_FILE"] = str(_HOME / "providers.yaml")
os.environ["USAGE_MONITOR_PRICES_FILE"] = str(_HOME / "prices.yaml")
os.environ["USAGE_MONITOR_OVERRIDES_FILE"] = str(_HOME / "overrides.json")
os.environ["USAGE_MONITOR_REFRESH_INTERVAL"] = "0"

# Safe default providers file: a single placeholder provider with no
# credential, so collect_status() never hits the network or keychain.
(_HOME / "providers.yaml").write_text(
    "providers:\n"
    "  - id: demo\n"
    "    label: Demo Provider\n"
    "    type: placeholder\n"
    "    message: test placeholder\n",
    encoding="utf-8",
)

from usage_monitor_app import core  # noqa: E402


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Redirect snapshot persistence and external-adapter loading to tmp_path.

    Returns the per-test snapshot file path.
    """
    snap = tmp_path / "snapshots.jsonl"
    monkeypatch.setattr(core, "SNAPSHOT_FILE", snap)
    monkeypatch.setattr(core, "ADAPTER_DIR", tmp_path / "no-adapters")
    return snap


@pytest.fixture()
def providers_file(tmp_path):
    """Helper to write a providers config file under tmp_path."""

    def _write(text: str, name: str = "providers.yaml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write

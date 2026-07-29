# SPDX-License-Identifier: MIT
"""Packaging metadata tests."""
from __future__ import annotations

from pathlib import Path


def test_cli_entry_points_include_public_and_legacy_names():
    text = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'usage-monitor = "scripts.usagectl:main"' in text
    assert 'usagectl = "scripts.usagectl:main"' in text

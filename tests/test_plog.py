# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from usage_monitor_app import plog
from usage_monitor_app.plog import LogSettings


def test_plog_only_filters_providers(tmp_path: Path):
    logf = tmp_path / "t.log"
    plog.configure(LogSettings(enabled=True, level="debug", path=logf, only={"antigravity"}))
    with plog.provider_scope({"id": "antigravity", "type": "antigravity"}):
        plog.debug("agy line", port=1)
    with plog.provider_scope({"id": "deepseek", "type": "deepseek"}):
        plog.debug("ds line")
    text = logf.read_text(encoding="utf-8")
    assert "agy line" in text
    assert "ds line" not in text


def test_plog_per_entry_log_flag(tmp_path: Path):
    logf = tmp_path / "t2.log"
    plog.configure(LogSettings(enabled=False, level="info", path=logf, only=set()))
    with plog.provider_scope({"id": "codex", "type": "codex", "log": True, "log_level": "debug"}):
        plog.debug("forced")
    with plog.provider_scope({"id": "deepseek", "type": "deepseek"}):
        plog.info("silent")
    text = logf.read_text(encoding="utf-8")
    assert "forced" in text
    assert "silent" not in text


def test_plog_redacts_secrets(tmp_path: Path):
    logf = tmp_path / "t3.log"
    plog.configure(LogSettings(enabled=True, level="info", path=logf, only=set()))
    with plog.provider_scope({"id": "x", "type": "y"}):
        plog.info("tok", access_token="secret-value", refresh_token="also")
    text = logf.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert "<redacted>" in text

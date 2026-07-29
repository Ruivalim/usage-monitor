# SPDX-License-Identifier: MIT
"""Tests for the hermes-state-db read-only SQLite adapter.

All databases are temporary sqlite files built in tmp_path. The adapter is
read-only (SQLite URI mode=ro), never touches the network, and never sees
real credentials.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usage_monitor_app import core

_SCHEMA = """
CREATE TABLE session_model_usage (
    session_id TEXT,
    model TEXT,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    task TEXT,
    api_call_count INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    profile_name TEXT,
    started_at TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL
);
"""

_NOW = datetime.now(timezone.utc)
_RECENT = _NOW.isoformat()
_OLD = (_NOW - timedelta(days=40)).isoformat()


def _row(
    session_id: str = "s1",
    model: str = "gpt-4o",
    provider: str = "openai",
    calls: int = 2,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
    est: float | None = 0.01,
    actual: float | None = None,
    last_seen: str = _RECENT,
) -> tuple:
    return (
        session_id, model, provider, "https://api.example.com", "api", "chat",
        calls, input_tokens, output_tokens, cache_read, cache_write, reasoning,
        est, actual, "estimated" if actual is None else "actual", "tokens",
        last_seen, last_seen,
    )


def _make_db(path: Path, rows: list[tuple], sessions: list[tuple] | None = None, schema: str = _SCHEMA) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        if rows:
            conn.executemany(
                "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        for sess in sessions or []:
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", sess)
        conn.commit()
    finally:
        conn.close()
    return path


def _conf(db: Path, **extra):
    return {"id": "hermes-state", "label": "Hermes state", "type": "hermes-state-db", "path": str(db), **extra}


def _run(conf):
    return core.REGISTRY["hermes-state-db"](conf)


def test_aggregates_by_provider_and_model(tmp_path):
    db = _make_db(tmp_path / "state.db", [
        _row(model="gpt-4o", provider="openai", calls=2, est=0.01),
        _row(model="gpt-4o", provider="openai", calls=3, est=0.02),
        _row(model="deepseek-chat", provider="deepseek", calls=1, est=0.005),
    ])
    out = _run(_conf(db))
    assert [p.id for p in out] == ["hermes-state:deepseek", "hermes-state:openai"]
    for p in out:
        assert p.status == "ok"
        assert p.source == "hermes-state-db"
    openai = next(p for p in out if p.id.endswith(":openai"))
    assert openai.usage == core.Money(pytest.approx(0.03), "USD")
    assert "5 calls" in (openai.message or "")
    assert any("gpt-4o" in d for d in openai.details)


def test_actual_cost_preferred_over_estimated(tmp_path):
    db = _make_db(tmp_path / "state.db", [
        _row(est=0.50, actual=0.42),
    ])
    out = _run(_conf(db))
    assert len(out) == 1
    assert out[0].usage == core.Money(pytest.approx(0.42), "USD")
    assert "actual" in (out[0].message or "")


def test_missing_db_is_unavailable(tmp_path):
    out = _run(_conf(tmp_path / "nope.db"))
    assert len(out) == 1
    assert out[0].status == "unavailable"
    assert "not found" in (out[0].message or "")


def test_schema_drift_missing_table_is_unknown(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE something_else (id TEXT)")
    conn.close()
    out = _run(_conf(path))
    assert out[0].status == "unknown"
    assert "schema drift" in (out[0].message or "")


def test_schema_drift_missing_column_is_unknown(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT)")
    conn.close()
    out = _run(_conf(path))
    assert out[0].status == "unknown"
    assert "missing columns" in (out[0].message or "")


def test_limit_days_filters_old_rows(tmp_path):
    db = _make_db(tmp_path / "state.db", [
        _row(model="new-model", est=0.01, last_seen=_RECENT),
        _row(model="old-model", est=0.99, last_seen=_OLD),
    ])
    out = _run(_conf(db, limit_days=7))
    assert len(out) == 1
    assert out[0].usage == core.Money(pytest.approx(0.01), "USD")
    assert any("new-model" in d for d in out[0].details)
    assert not any("old-model" in d for d in out[0].details)


def test_limit_days_with_no_recent_rows_reports_ok_empty(tmp_path):
    db = _make_db(tmp_path / "state.db", [_row(last_seen=_OLD)])
    out = _run(_conf(db, limit_days=7))
    assert len(out) == 1
    assert out[0].status == "ok"
    assert "No usage rows" in (out[0].message or "")


def test_epoch_timestamps_are_accepted(tmp_path):
    db = _make_db(tmp_path / "state.db", [
        _row(last_seen=str((_NOW - timedelta(days=2)).timestamp())),
    ])
    out = _run(_conf(db, limit_days=7))
    assert len(out) == 1
    assert out[0].status == "ok"


def test_profile_filter_selects_matching_sessions(tmp_path):
    db = _make_db(
        tmp_path / "state.db",
        [
            _row(session_id="s1", model="m-work", est=0.01),
            _row(session_id="s2", model="m-play", est=0.02),
        ],
        sessions=[
            ("s1", "work", _RECENT, 0, 0, 0.0),
            ("s2", "play", _RECENT, 0, 0, 0.0),
        ],
    )
    out = _run(_conf(db, profile="work"))
    assert len(out) == 1
    assert any("m-work" in d for d in out[0].details)
    assert not any("m-play" in d for d in out[0].details)


def test_profile_filter_without_sessions_table_is_unknown(tmp_path):
    schema = _SCHEMA.replace("CREATE TABLE sessions", "CREATE TABLE sessions_unused")\
        .replace("INSERT INTO sessions", "INSERT INTO sessions_unused")
    db = _make_db(tmp_path / "state.db", [_row()], schema=schema)
    out = _run(_conf(db, profile="work"))
    assert out[0].status == "unknown"
    assert "profile" in (out[0].message or "")


def test_multiple_db_paths_aggregate(tmp_path):
    db1 = _make_db(tmp_path / "a.db", [_row(calls=2, est=0.01)])
    db2 = _make_db(tmp_path / "b.db", [_row(calls=3, est=0.02)])
    out = _run({"id": "hermes-state", "label": "Hermes state", "type": "hermes-state-db",
                "db_paths": [str(db1), str(db2), str(tmp_path / "missing.db")]})
    assert len(out) == 1
    assert out[0].usage == core.Money(pytest.approx(0.03), "USD")
    assert "5 calls" in (out[0].message or "")
    assert any("skipped missing db" in d for d in out[0].details)


def test_readonly_file_is_readable_without_write_lock(tmp_path):
    db = _make_db(tmp_path / "state.db", [_row()])
    os.chmod(db, 0o444)
    try:
        out = _run(_conf(db))
    finally:
        os.chmod(db, 0o644)
    assert out[0].status == "ok"


def test_price_table_estimate_fills_missing_costs(tmp_path, monkeypatch):
    prices = tmp_path / "prices.yaml"
    prices.write_text(
        "version: 1\n"
        "providers:\n"
        "  openai:\n"
        "    currency: USD\n"
        "    models:\n"
        "      gpt-4o:\n"
        "        input: 2.5\n"
        "        output: 10.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "PRICES_FILE", prices)
    db = _make_db(tmp_path / "state.db", [
        _row(est=None, actual=None, input_tokens=1_000_000, output_tokens=100_000),
    ])
    out = _run(_conf(db))
    assert len(out) == 1
    assert out[0].usage is None  # no USD cost recorded in state.db
    # 1M input @2.5 + 100k output @10.0 = 3.50 USD, reported separately
    assert any("price-table estimate: 3.5000 USD" in d for d in out[0].details)


def test_price_table_estimate_keeps_foreign_currency_out_of_usage(tmp_path, monkeypatch):
    prices = tmp_path / "prices.yaml"
    prices.write_text(
        "version: 1\n"
        "providers:\n"
        "  deepseek:\n"
        "    currency: CNY\n"
        "    models:\n"
        "      deepseek-chat:\n"
        "        input: 2.0\n"
        "        output: 8.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "PRICES_FILE", prices)
    db = _make_db(tmp_path / "state.db", [
        _row(provider="deepseek", model="deepseek-chat", est=None, actual=None,
             input_tokens=1_000_000, output_tokens=0),
    ])
    out = _run(_conf(db))
    assert out[0].usage is None  # CNY estimate must not leak into the USD figure
    assert any("CNY" in d for d in out[0].details)


def test_dispatch_via_collect_status(tmp_path, providers_file, isolated_state):
    db = _make_db(tmp_path / "state.db", [_row()])
    path = providers_file(
        "providers:\n"
        "  - id: hermes-state\n"
        "    label: Hermes state\n"
        "    type: hermes-state-db\n"
        f"    path: {db}\n"
    )
    snap = core.collect_status(persist=False, providers_file=path)
    assert len(snap.providers) == 1
    p = snap.providers[0]
    assert p.id == "hermes-state:openai"
    assert p.status == "ok"
    assert snap.overall == "ok"

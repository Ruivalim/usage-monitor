# SPDX-License-Identifier: MIT
"""FastAPI endpoints via TestClient (no server, no network)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from usage_monitor_app.web import API_PREFIX, create_app
from usage_monitor_app.config import AppConfig, AuthConfig

API = API_PREFIX


@pytest.fixture()
def client(isolated_state):
    # The default providers file (see conftest) is a single placeholder
    # provider, so every collect_status call stays offline.
    return TestClient(create_app())


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "API Usage Monitor" in resp.text


def test_dashboard_fetches_stay_relative(client):
    # The same document is served under the Hermes plugin prefix, so absolute
    # API paths would break that mount.
    html = client.get("/").text
    assert "api('./status/cached')" in html
    assert "'/api/v1" not in html


def test_status_endpoint_shape(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] in {"ok", "unknown", "warning", "error"}
    assert isinstance(data["providers"], list)
    ids = [p["id"] for p in data["providers"]]
    assert ids == ["demo"]  # from the conftest providers file
    assert data["providers"][0]["status"] in {"unknown", "unavailable"}


def test_status_does_not_persist_by_default(client, isolated_state):
    client.get("/status")
    assert not isolated_state.exists()


def test_status_persist_query_param(client, isolated_state):
    resp = client.get("/status", params={"persist": "true"})
    assert resp.status_code == 200
    assert isolated_state.exists()
    assert len(isolated_state.read_text().splitlines()) == 1


def test_refresh_persists_snapshot(client, isolated_state):
    resp = client.post("/refresh")
    assert resp.status_code == 200
    assert len(isolated_state.read_text().splitlines()) == 1


def test_latest_and_history(client, isolated_state):
    client.post("/refresh")
    client.post("/refresh")
    for path in ("/latest", "/history"):
        resp = client.get(path, params={"limit": 10})
        assert resp.status_code == 200
        snaps = resp.json()["snapshots"]
        assert len(snaps) == 2
        assert snaps[0]["providers"][0]["id"] == "demo"


def test_latest_empty_history(client):
    resp = client.get("/latest")
    assert resp.status_code == 200
    assert resp.json() == {"snapshots": []}


def test_latest_limit_validation(client):
    assert client.get("/latest", params={"limit": 0}).status_code == 422
    assert client.get("/latest", params={"limit": 501}).status_code == 422


def test_status_text(client):
    resp = client.get("/status.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "API Usage Monitor" in resp.text
    assert "Demo Provider" in resp.text


def test_status_json_pretty(client):
    resp = client.get("/status.json")
    assert resp.status_code == 200
    data = json.loads(resp.text)
    assert data["providers"][0]["id"] == "demo"
    assert "\n" in resp.text  # pretty by default
    compact = client.get("/status.json", params={"pretty": "false"})
    assert "\n" not in compact.text.strip()


def test_overrides_endpoints_roundtrip(client, tmp_path, monkeypatch):
    from usage_monitor_app import core

    monkeypatch.setattr(core, "OVERRIDES_FILE", tmp_path / "overrides.json")
    assert client.get("/overrides").json() == {}
    resp = client.post("/overrides/demo", json={"relevant": False})
    assert resp.status_code == 200
    assert resp.json() == {"demo": {"relevant": False}}
    assert client.get("/overrides").json() == {"demo": {"relevant": False}}
    # The next status collection marks the provider as muted.
    status = client.get("/status").json()
    assert status["providers"][0]["relevant"] is False
    client.post("/overrides/demo", json={"relevant": True})
    assert client.get("/overrides").json() == {}
    status = client.get("/status").json()
    assert status["providers"][0]["relevant"] is True


def test_autostart_endpoints_are_delegated(client, monkeypatch):
    from usage_monitor_app import web

    monkeypatch.setattr(web._autostart, "agent_status", lambda config: {"agents": [], "installed": False})
    monkeypatch.setattr(web._autostart, "install_agents", lambda config: {"written": [], "menubar_bootstrapped": True})
    monkeypatch.setattr(web._autostart, "uninstall_agents", lambda config: {"removed": []})

    assert client.get("/autostart").json() == {"agents": [], "installed": False}
    assert client.post("/autostart/install").json()["menubar_bootstrapped"] is True
    assert client.post("/autostart/uninstall").json() == {"removed": []}


def test_status_cached_falls_back_to_collect_when_empty(client):
    resp = client.get("/status/cached")
    assert resp.status_code == 200
    assert resp.json()["providers"][0]["id"] == "demo"


def test_status_cached_reads_latest_snapshot(client, isolated_state):
    client.post("/refresh")
    resp = client.get("/status/cached")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"][0]["id"] == "demo"
    assert "checked_at" in data


# --- /api/v1 aliases -------------------------------------------------------
# The versioned prefix is the stable public surface; the unprefixed routes stay
# for backwards compatibility and for the dashboard's relative fetches.


@pytest.mark.parametrize(
    "path",
    ["/status", "/status/cached", "/latest", "/history", "/status.txt", "/status.json", "/overrides"],
)
def test_api_v1_get_aliases_exist(client, path):
    resp = client.get(API + path)
    assert resp.status_code == 200


def test_api_v1_status_matches_legacy_shape(client):
    data = client.get(API + "/status").json()
    assert data["overall"] in {"ok", "unknown", "warning", "error"}
    assert [p["id"] for p in data["providers"]] == ["demo"]


def test_api_v1_status_does_not_persist_by_default(client, isolated_state):
    client.get(API + "/status")
    assert not isolated_state.exists()


def test_api_v1_status_persist_query_param(client, isolated_state):
    assert client.get(API + "/status", params={"persist": "true"}).status_code == 200
    assert len(isolated_state.read_text().splitlines()) == 1


def test_api_v1_refresh_persists_snapshot(client, isolated_state):
    assert client.post(API + "/refresh").status_code == 200
    assert len(isolated_state.read_text().splitlines()) == 1


def test_api_v1_latest_and_history(client, isolated_state):
    client.post(API + "/refresh")
    client.post(API + "/refresh")
    for path in (API + "/latest", API + "/history"):
        snaps = client.get(path, params={"limit": 10}).json()["snapshots"]
        assert len(snaps) == 2
        assert snaps[0]["providers"][0]["id"] == "demo"


def test_api_v1_latest_limit_validation(client):
    assert client.get(API + "/latest", params={"limit": 0}).status_code == 422
    assert client.get(API + "/latest", params={"limit": 501}).status_code == 422


def test_api_v1_status_text_and_json(client):
    text = client.get(API + "/status.txt")
    assert text.headers["content-type"].startswith("text/plain")
    assert "API Usage Monitor" in text.text
    data = json.loads(client.get(API + "/status.json").text)
    assert data["providers"][0]["id"] == "demo"


def test_api_v1_overrides_roundtrip(client, tmp_path, monkeypatch):
    from usage_monitor_app import core

    monkeypatch.setattr(core, "OVERRIDES_FILE", tmp_path / "overrides.json")
    assert client.get(API + "/overrides").json() == {}
    resp = client.post(API + "/overrides/demo", json={"relevant": False})
    assert resp.status_code == 200
    assert resp.json() == {"demo": {"relevant": False}}
    # Both prefixes read the same override store.
    assert client.get("/overrides").json() == {"demo": {"relevant": False}}
    client.post("/overrides/demo", json={"relevant": True})
    assert client.get(API + "/overrides").json() == {}


def test_api_v1_autostart_endpoints_are_delegated(client, monkeypatch):
    from usage_monitor_app import web

    monkeypatch.setattr(web._autostart, "agent_status", lambda config: {"agents": [], "installed": False})
    monkeypatch.setattr(web._autostart, "uninstall_agents", lambda config: {"removed": []})

    assert client.get(API + "/autostart").json() == {"agents": [], "installed": False}
    assert client.post(API + "/autostart/uninstall").json() == {"removed": []}


def test_openapi_documents_both_prefixes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/status" in paths
    assert API + "/status" in paths
    assert API + "/overrides/{provider_id}" in paths


def test_basic_auth_protects_dashboard_api_data(isolated_state):
    auth_client = TestClient(create_app(AppConfig(auth=AuthConfig(enabled=True, username="local", password="secret"))))
    assert auth_client.get("/").status_code == 200
    assert auth_client.get("/config").json()["auth_required"] is True
    assert auth_client.get("/status").status_code == 401
    assert auth_client.get(API + "/status").status_code == 401
    ok = auth_client.get("/status", auth=("local", "secret"))
    assert ok.status_code == 200
    assert ok.json()["providers"][0]["id"] == "demo"


def test_dashboard_uses_config_language():
    pt = TestClient(create_app(AppConfig()))
    assert '<html lang="en">' in pt.get("/").text

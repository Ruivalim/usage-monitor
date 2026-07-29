# SPDX-License-Identifier: MIT
"""Hermes plugin API routes (mounted by Hermes, no real gateway needed)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.plugin_api import router


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_plugin_config_is_public_and_sanitized(isolated_state):
    data = client().get("/config").json()

    assert data["auth_required"] is False
    assert "dashboard" in data
    assert "password" not in str(data).lower()
    assert "credential" not in str(data).lower()


def test_plugin_status_still_serves_provider_shape(isolated_state):
    data = client().get("/status").json()

    assert data["providers"][0]["id"] == "demo"
    assert data["providers"][0]["status"] in {"unknown", "unavailable"}

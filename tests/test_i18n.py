# SPDX-License-Identifier: MIT
"""The shared translation table and the two surfaces that render from it."""
from __future__ import annotations

import pytest

from usage_monitor_app import i18n, menubar
from usage_monitor_app.config import DashboardConfig, AppConfig, load_app_config
from usage_monitor_app.web import dashboard_html


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("pt", "pt-BR"),
        ("pt-BR", "pt-BR"),
        ("pt_BR", "pt-BR"),
        ("pt-BR.UTF-8", "pt-BR"),
        ("en_US", "en"),
        ("klingon", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_normalize_language(raw, expected):
    assert i18n.normalize_language(raw) == expected


def test_env_overrides_configured_language(monkeypatch):
    monkeypatch.setenv("USAGE_MONITOR_LANGUAGE", "pt")
    assert i18n.resolve_language("en") == "pt-BR"
    monkeypatch.delenv("USAGE_MONITOR_LANGUAGE")
    assert i18n.resolve_language("en") == "en"


def test_every_language_covers_every_key():
    english = set(i18n.TRANSLATIONS["en"])
    for tag in i18n.SUPPORTED_LANGUAGES:
        # Missing keys are legal (English fills them), unknown ones are typos.
        assert set(i18n.TRANSLATIONS[tag]) <= english, tag
        assert english <= set(i18n.translations(tag))


def test_missing_key_falls_back_to_english(monkeypatch):
    monkeypatch.setitem(i18n.TRANSLATIONS, "pt-BR", {"dashboard.refresh": "Atualizar"})
    table = i18n.translations("pt-BR")
    assert table["dashboard.refresh"] == "Atualizar"
    assert table["dashboard.history"] == i18n.TRANSLATIONS["en"]["dashboard.history"]


def test_translator_interpolates_and_keeps_unknown_keys():
    t = i18n.translator("en")
    assert t("tray.remaining", label="Weekly", percent="58") == "Weekly: 58% left"
    assert t("nope.not.a.key") == "nope.not.a.key"


def test_config_language_is_a_canonical_tag(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("dashboard:\n  language: pt_BR\n", encoding="utf-8")
    assert load_app_config(path).dashboard.language == "pt-BR"


def test_dashboard_inlines_the_configured_language():
    html = dashboard_html(AppConfig(dashboard=DashboardConfig(language="pt-BR")))
    assert '<html lang="pt-BR">' in html
    assert "Histórico" in html
    assert '"dashboard.refresh": "Atualizar"' in html
    # English is not shipped alongside it: one language per render.
    assert '"dashboard.history": "History"' not in html


def test_dashboard_never_lets_a_string_close_the_script_tag(monkeypatch):
    monkeypatch.setitem(i18n.TRANSLATIONS["en"], "dashboard.refresh", "</script><b>x")
    html = dashboard_html(AppConfig())
    assert "</script><b>x" not in html
    assert "<\\/script>" in html


def test_tray_renders_the_same_table_in_portuguese():
    provider = {
        "id": "kimi",
        "label": "Kimi",
        "status": "ok",
        "relevant": False,
        "windows": [{"label": "Semana", "remaining_percent": 58, "days_until_reset": 3}],
    }
    line = menubar.provider_line_from_dict(provider, language="pt-BR")
    assert "Semana: 58% restante" in line
    assert "reset em 3d" in line
    assert line.endswith("(mudo)")

# SPDX-License-Identifier: MIT
"""Translations for the dashboard and the macOS tray (``en`` and ``pt-BR``).

One flat key/value map per language. ``en`` is the source of truth: every
other language is merged *over* it, so a missing or newly added key falls
back to English instead of rendering an empty string or a raw key.

Language resolution order (first non-empty wins)::

    USAGE_MONITOR_LANG / USAGE_MONITOR_LANGUAGE   env
    language: pt-BR                               config.yaml
    en                                            built-in default

The default stays ``en`` because this is an open-source project; ``pt-BR`` is
a one-line opt-in for local use.

Keys are namespaced by surface (``dashboard.*``, ``tray.*``) and carry
``{placeholder}`` slots where a value is interpolated. The dashboard sends the
whole map to the browser as JSON and interpolates client-side, so the strings
below are the single source for both the server-rendered shell and the JS.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping

DEFAULT_LANGUAGE = "en"

# Canonical tag -> the spellings we accept for it. Matching is done on a
# lowercased, `_`-to-`-` normalized value, longest prefix first, so `pt`,
# `pt_BR`, `pt-br` and `pt-BR.UTF-8` all land on `pt-BR`.
_ALIASES = {
    "pt-BR": ("pt-br", "pt", "pt-pt", "portuguese", "português", "portugues"),
    "en": ("en", "en-us", "en-gb", "english", "c", "posix"),
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # --- shared ---------------------------------------------------------
        "app.title": "API Usage Monitor",
        "html.lang": "en",
        # --- dashboard shell ------------------------------------------------
        "dashboard.loading": "loading…",
        "dashboard.collecting": "collecting…",
        "dashboard.show_auth": "show auth",
        "dashboard.show_auth_title": "Show auth:* providers from hermes-auth",
        "dashboard.startup_loading": "startup…",
        "dashboard.logout": "Sign out",
        "dashboard.refresh": "Refresh",
        "dashboard.refreshing": "collecting…",
        "dashboard.history": "History",
        "dashboard.history_hint": "height = number of alerts per snapshot",
        # --- login gate -----------------------------------------------------
        "dashboard.gate_intro": "This dashboard requires authentication.",
        "dashboard.username": "username",
        "dashboard.password": "password",
        "dashboard.sign_in": "Sign in",
        "dashboard.invalid_credentials": "Invalid credentials.",
        "dashboard.session_expired": "Session expired or invalid credentials.",
        # --- stats ----------------------------------------------------------
        "dashboard.stat_providers": "providers",
        "dashboard.stat_ok": "ok",
        "dashboard.stat_warn": "warning",
        "dashboard.stat_err": "error",
        "dashboard.stat_idle": "no data / muted",
        # --- provider cards -------------------------------------------------
        "dashboard.balance": "balance",
        "dashboard.spent": "spent",
        "dashboard.no_data": "no data",
        "dashboard.remaining": "{percent}% left",
        "dashboard.reset_in": " · resets in {label}",
        "dashboard.reset_now": "now",
        "dashboard.muted_title": "Relevance off: excluded from overall/alerts. Click to re-enable",
        "dashboard.relevant_title": "Relevant: counts towards overall/alerts. Click to mute",
        "dashboard.empty_providers": 'No visible providers. Configure <code>providers.yaml</code> or turn on "show auth".',
        # --- history --------------------------------------------------------
        "dashboard.empty_history": "No snapshots yet. Click Refresh.",
        "dashboard.history_bar_title": "{when}: {count} alerts · {overall}",
        # --- meta / startup -------------------------------------------------
        "dashboard.meta": "collected {stamp} · read {ago}s ago",
        "dashboard.startup_on": "startup: on",
        "dashboard.startup_off": "startup: off",
        "dashboard.startup_unknown": "startup: ?",
        "dashboard.startup_tray_active": " (tray running)",
        "dashboard.startup_on_title": "Remove the startup LaunchAgents (the running server keeps going until logout)",
        "dashboard.startup_off_title": "Install LaunchAgents (server + tray) to start at login",
        "dashboard.startup_confirm": "Remove server and tray from startup? The running server keeps going until logout.",
        # --- tray -----------------------------------------------------------
        "tray.refresh_now": "Refresh Now",
        "tray.open_dashboard": "Open Dashboard",
        "tray.show_auth": "Show auth",
        "tray.start_at_login": "Start at Login",
        "tray.quit": "Quit Usage Monitor",
        "tray.collecting": "collecting…",
        "tray.no_providers": "No providers configured",
        "tray.refresh_failed": "⚠️ refresh failed: {error}",
        "tray.startup_error": "startup: {error}",
    },
    "pt-BR": {
        "html.lang": "pt-BR",
        "dashboard.loading": "carregando…",
        "dashboard.collecting": "coletando…",
        "dashboard.show_auth": "mostrar auth",
        "dashboard.show_auth_title": "Mostrar providers auth:* do hermes-auth",
        "dashboard.startup_loading": "startup…",
        "dashboard.logout": "Sair",
        "dashboard.refresh": "Atualizar",
        "dashboard.refreshing": "coletando…",
        "dashboard.history": "Histórico",
        "dashboard.history_hint": "altura = nº de alerts por snapshot",
        "dashboard.gate_intro": "Este dashboard exige autenticação.",
        "dashboard.username": "usuário",
        "dashboard.password": "senha",
        "dashboard.sign_in": "Entrar",
        "dashboard.invalid_credentials": "Credenciais inválidas.",
        "dashboard.session_expired": "Sessão expirada ou credenciais inválidas.",
        "dashboard.stat_providers": "providers",
        "dashboard.stat_ok": "ok",
        "dashboard.stat_warn": "atenção",
        "dashboard.stat_err": "erro",
        "dashboard.stat_idle": "sem dado / mudos",
        "dashboard.balance": "saldo",
        "dashboard.spent": "gasto",
        "dashboard.no_data": "sem dados",
        "dashboard.remaining": "{percent}% restante",
        "dashboard.reset_in": " · reset em {label}",
        "dashboard.reset_now": "agora",
        "dashboard.muted_title": "Relevância desligada: fora do overall/alertas. Clique para reativar",
        "dashboard.relevant_title": "Relevante: conta no overall/alertas. Clique para silenciar",
        "dashboard.empty_providers": 'Nenhum provider visível. Configure <code>providers.yaml</code> ou ative "mostrar auth".',
        "dashboard.empty_history": "Sem snapshots ainda. Clique em Atualizar.",
        "dashboard.history_bar_title": "{when}: {count} alerts · {overall}",
        "dashboard.meta": "coletado {stamp} · lido há {ago}s",
        "dashboard.startup_on": "startup: on",
        "dashboard.startup_off": "startup: off",
        "dashboard.startup_unknown": "startup: ?",
        "dashboard.startup_tray_active": " (tray ativo)",
        "dashboard.startup_on_title": "Remover os LaunchAgents do startup (o servidor atual continua rodando até o logout)",
        "dashboard.startup_off_title": "Instalar LaunchAgents (server + tray) para abrir no startup",
        "dashboard.startup_confirm": "Remover server e tray do startup? O servidor atual continua rodando até o logout.",
        "tray.refresh_now": "Atualizar agora",
        "tray.open_dashboard": "Abrir dashboard",
        "tray.show_auth": "Mostrar auth",
        "tray.start_at_login": "Abrir no startup",
        "tray.quit": "Sair do Usage Monitor",
        "tray.collecting": "coletando…",
        "tray.no_providers": "Nenhum provider configurado",
        "tray.refresh_failed": "⚠️ falha ao atualizar: {error}",
        "tray.startup_error": "startup: {error}",
    },
}

SUPPORTED_LANGUAGES = tuple(TRANSLATIONS)


def normalize_language(value: Any) -> str:
    """Map an arbitrary language spelling onto a supported tag.

    Unknown or empty values fall back to :data:`DEFAULT_LANGUAGE` rather than
    raising: a typo in ``config.yaml`` must not stop the dashboard from
    rendering.
    """
    if not value:
        return DEFAULT_LANGUAGE
    text = str(value).strip().lower().replace("_", "-")
    if not text:
        return DEFAULT_LANGUAGE
    # Drop POSIX locale decorations: `pt_BR.UTF-8`, `en_US@euro`.
    text = text.split(".", 1)[0].split("@", 1)[0]
    for tag, aliases in _ALIASES.items():
        for alias in aliases:
            if text == alias or text.startswith(alias + "-"):
                return tag
    # `pt-BR` style tags we do not know: try the bare primary subtag.
    primary = text.split("-", 1)[0]
    for tag, aliases in _ALIASES.items():
        if primary in aliases:
            return tag
    return DEFAULT_LANGUAGE


def language_from_env() -> str | None:
    """Language requested via the environment, if any."""
    for name in ("USAGE_MONITOR_LANG", "USAGE_MONITOR_LANGUAGE"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return normalize_language(raw)
    return None


def resolve_language(configured: Any = None) -> str:
    """Env override first, then the configured value, then ``en``."""
    return language_from_env() or normalize_language(configured)


def translations(language: Any = None) -> dict[str, str]:
    """Full key/value map for ``language``, with English filling any gaps."""
    tag = normalize_language(language)
    merged = dict(TRANSLATIONS[DEFAULT_LANGUAGE])
    if tag != DEFAULT_LANGUAGE:
        merged.update(TRANSLATIONS[tag])
    return merged


def interpolate(template: str, variables: Mapping[str, Any] | None = None) -> str:
    """Fill ``{name}`` slots. Unknown slots are left as-is, never an error."""
    if not variables:
        return template
    out = template
    for name, value in variables.items():
        out = out.replace("{" + name + "}", str(value))
    return out


def translator(language: Any = None) -> Callable[..., str]:
    """Return ``t(key, **variables)`` for ``language``.

    An unknown key returns the key itself, which makes a missing translation
    visible in the UI instead of silently blank.
    """
    table = translations(language)

    def t(key: str, **variables: Any) -> str:
        return interpolate(table.get(key, key), variables)

    return t

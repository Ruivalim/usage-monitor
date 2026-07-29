"""Agent-side registration stub for the optional Hermes integration.

Hermes only discovers a plugin directory that holds a ``plugin.yaml`` *and* an
``__init__.py`` exposing ``register(ctx)``. Discovery is what makes
``hermes plugins enable api-usage-monitor`` succeed, which in turn adds the
plugin to ``plugins.enabled`` in config.yaml — the gate the web server checks
before it mounts ``dashboard/plugin_api.py`` under ``/api/plugins/<name>``.

This plugin contributes no agent hooks and no tools: the useful surfaces are
that REST backend and the Desktop UI in ``plugin/desktop/plugin.js``, which
calls it through ``ctx.rest``. So ``register`` is intentionally a no-op.
"""

from __future__ import annotations


def register(ctx) -> None:  # noqa: ARG001 - required by the plugin contract
    """No agent hooks; see the module docstring."""
    return None

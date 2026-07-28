#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_NAME="api-usage-monitor"
PLUGIN_DIR="${HERMES_HOME}/plugins/${PLUGIN_NAME}"
SKILL_DIR="${HERMES_HOME}/skills/productivity/api-usage-monitoring"

echo "=== Hermes Usage Monitor — Uninstall ==="

# 1. Disable plugin
if command -v hermes &>/dev/null; then
    echo "Disabling plugin..."
    hermes plugins disable "${PLUGIN_NAME}" 2>/dev/null || true
fi

# 2. Remove plugin dir
if [ -d "${PLUGIN_DIR}" ]; then
    echo "Removing ${PLUGIN_DIR}..."
    rm -rf "${PLUGIN_DIR}"
fi

# 3. Remove desktop plugin
DESKTOP_PLUGIN="${HERMES_HOME}/desktop-plugins/${PLUGIN_NAME}"
if [ -d "${DESKTOP_PLUGIN}" ]; then
    echo "Removing ${DESKTOP_PLUGIN}..."
    rm -rf "${DESKTOP_PLUGIN}"
fi

# 4. Remove skill
if [ -d "${SKILL_DIR}" ]; then
    echo "Removing ${SKILL_DIR}..."
    rm -rf "${SKILL_DIR}"
fi

# 5. Remove CLI wrapper
if [ -f "${HOME}/.local/bin/usagemon" ]; then
    echo "Removing ${HOME}/.local/bin/usagemon..."
    rm -f "${HOME}/.local/bin/usagemon"
fi

# 6. Leave adapters and snapshots (user customizations)
echo ""
echo "Done."
echo "Note: ~/.hermes/usage/adapters/ preserved (may contain custom adapters)"
echo "      ~/.hermes/usage/snapshots.jsonl preserved"
echo "      ~/.hermes/usage/launchagents/ preserved (example templates; remove manually if unwanted)"
echo "      Any plists you copied into ~/Library/LaunchAgents are untouched; unload with launchctl bootout first"

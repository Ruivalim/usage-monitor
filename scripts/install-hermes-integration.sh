#!/usr/bin/env bash
# Optional integration: expose this standalone app to a Hermes install as a
# skill + plugin. The app never needs this script — `make install` sets up the
# standalone LaunchAgents and `make install-cli` puts `usagemon` on PATH.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_NAME="api-usage-monitor"
PLUGIN_DIR="${HERMES_HOME}/plugins/${PLUGIN_NAME}/dashboard"
SKILL_DIR="${HERMES_HOME}/skills/productivity/api-usage-monitoring"
ADAPTERS_DIR="${HERMES_HOME}/usage/adapters"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Usage Monitor — Hermes integration install ==="
echo ""

# 1. Plugin core
echo "[1/6] Installing plugin core..."
mkdir -p "${PLUGIN_DIR}"
cp -f "${REPO_DIR}/plugin/manifest.json" "${PLUGIN_DIR}/"
cp -f "${REPO_DIR}/plugin/usage_monitor.py" "${PLUGIN_DIR}/"
cp -f "${REPO_DIR}/plugin/plugin_api.py" "${PLUGIN_DIR}/"
rm -rf "${PLUGIN_DIR}/usage_monitor_app"
cp -R "${REPO_DIR}/usage_monitor_app" "${PLUGIN_DIR}/"
echo "       → ${PLUGIN_DIR}"

# 2. External adapters (CLI-based)
echo "[2/6] Installing CLI adapters..."
mkdir -p "${ADAPTERS_DIR}"
mkdir -p "${HERMES_HOME}/usage"
if [ ! -f "${HERMES_HOME}/usage/providers.example.yaml" ] && [ -f "${REPO_DIR}/examples/providers.yaml" ]; then
    cp -f "${REPO_DIR}/examples/providers.yaml" "${HERMES_HOME}/usage/providers.example.yaml"
fi
if [ ! -f "${HERMES_HOME}/usage/prices.example.yaml" ] && [ -f "${REPO_DIR}/examples/prices.yaml" ]; then
    cp -f "${REPO_DIR}/examples/prices.yaml" "${HERMES_HOME}/usage/prices.example.yaml"
fi
for adapter in claude_cli.py kimi_cli.py; do
    if [ -f "${REPO_DIR}/plugin/adapters/${adapter}" ]; then
        cp -f "${REPO_DIR}/plugin/adapters/${adapter}" "${ADAPTERS_DIR}/"
    fi
done
echo "       → ${ADAPTERS_DIR}"

# 3. CLI script
echo "[3/6] Installing usagectl.py..."
mkdir -p "${SKILL_DIR}/scripts"
cp -f "${REPO_DIR}/scripts/usagectl.py" "${SKILL_DIR}/scripts/"
chmod +x "${SKILL_DIR}/scripts/usagectl.py"
echo "       → ${SKILL_DIR}/scripts/"

# 4. Skill
echo "[4/6] Installing skill..."
cp -f "${REPO_DIR}/skills/api-usage-monitoring/SKILL.md" "${SKILL_DIR}/"
echo "       → ${SKILL_DIR}/SKILL.md"

# 5. LaunchAgent example templates (copied only — never loaded/enabled)
echo "[5/6] Installing LaunchAgent example templates..."
LAUNCHAGENTS_DIR="${HERMES_HOME}/usage/launchagents"
if [ -d "${REPO_DIR}/templates/launchagents" ]; then
    mkdir -p "${LAUNCHAGENTS_DIR}"
    cp -f "${REPO_DIR}/templates/launchagents/"*.plist "${LAUNCHAGENTS_DIR}/" 2>/dev/null || true
    cp -f "${REPO_DIR}/templates/launchagents/README.md" "${LAUNCHAGENTS_DIR}/" 2>/dev/null || true
fi
echo "       → ${LAUNCHAGENTS_DIR} (examples only; generate real plists with: usagectl.py autostart --output-dir <dir>)"

# 6. Enable plugin
echo "[6/6] Enabling plugin..."
if command -v hermes &>/dev/null; then
    hermes plugins enable "${PLUGIN_NAME}" 2>/dev/null && echo "       Plugin ${PLUGIN_NAME} enabled" || echo "       ⚠  Could not enable plugin (may need restart)"
else
    echo "       ⚠  'hermes' CLI not found — enable manually: hermes plugins enable ${PLUGIN_NAME}"
fi

echo ""
echo "Done! Hermes integration installed."
echo ""
echo "The standalone install is untouched. Quick test:"
echo "  ${REPO_DIR}/.venv/bin/python ${REPO_DIR}/scripts/usagectl.py status"

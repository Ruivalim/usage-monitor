#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_NAME="api-usage-monitor"
PLUGIN_DIR="${HERMES_HOME}/plugins/${PLUGIN_NAME}/dashboard"
SKILL_DIR="${HERMES_HOME}/skills/productivity/api-usage-monitoring"
ADAPTERS_DIR="${HERMES_HOME}/usage/adapters"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Hermes Usage Monitor — Install ==="
echo ""

# 1. Plugin core
echo "[1/7] Installing plugin core..."
mkdir -p "${PLUGIN_DIR}"
cp -f "${SCRIPT_DIR}/plugin/manifest.json" "${PLUGIN_DIR}/"
cp -f "${SCRIPT_DIR}/plugin/usage_monitor.py" "${PLUGIN_DIR}/"
cp -f "${SCRIPT_DIR}/plugin/plugin_api.py" "${PLUGIN_DIR}/"
rm -rf "${PLUGIN_DIR}/usage_monitor_app"
cp -R "${SCRIPT_DIR}/usage_monitor_app" "${PLUGIN_DIR}/"
echo "       → ${PLUGIN_DIR}"

# 2. External adapters (CLI-based)
echo "[2/7] Installing CLI adapters..."
mkdir -p "${ADAPTERS_DIR}"
mkdir -p "${HERMES_HOME}/usage"
if [ ! -f "${HERMES_HOME}/usage/providers.example.yaml" ] && [ -f "${SCRIPT_DIR}/examples/providers.yaml" ]; then
    cp -f "${SCRIPT_DIR}/examples/providers.yaml" "${HERMES_HOME}/usage/providers.example.yaml"
fi
if [ ! -f "${HERMES_HOME}/usage/prices.example.yaml" ] && [ -f "${SCRIPT_DIR}/examples/prices.yaml" ]; then
    cp -f "${SCRIPT_DIR}/examples/prices.yaml" "${HERMES_HOME}/usage/prices.example.yaml"
fi
for adapter in claude_cli.py kimi_cli.py; do
    if [ -f "${SCRIPT_DIR}/plugin/adapters/${adapter}" ]; then
        cp -f "${SCRIPT_DIR}/plugin/adapters/${adapter}" "${ADAPTERS_DIR}/"
    fi
done
echo "       → ${ADAPTERS_DIR}"

# 3. CLI wrapper
echo "[3/7] Installing usagectl.py..."
mkdir -p "${SKILL_DIR}/scripts"
cp -f "${SCRIPT_DIR}/scripts/usagectl.py" "${SKILL_DIR}/scripts/"
chmod +x "${SKILL_DIR}/scripts/usagectl.py"
echo "       → ${SKILL_DIR}/scripts/"

# 4. Skill
echo "[4/7] Installing skill..."
cp -f "${SCRIPT_DIR}/skills/api-usage-monitoring/SKILL.md" "${SKILL_DIR}/"
echo "       → ${SKILL_DIR}/SKILL.md"

# 5. CLI wrapper on PATH
echo "[5/7] Installing usagemon CLI wrapper..."
mkdir -p "${HOME}/.local/bin"
cat > "${HOME}/.local/bin/usagemon" <<'EOF'
#!/usr/bin/env bash
# usagemon — API Usage Monitor CLI (status, serve, menubar, autostart, latest)
PY="${HOME}/.hermes/hermes-agent/venv/bin/python"
[ -x "${PY}" ] || PY="$(command -v python3)"
exec "${PY}" "${HOME}/.hermes/skills/productivity/api-usage-monitoring/scripts/usagectl.py" "$@"
EOF
chmod +x "${HOME}/.local/bin/usagemon"
echo "       → ${HOME}/.local/bin/usagemon"

# 6. LaunchAgent example templates (copied only — never loaded/enabled)
echo "[6/7] Installing LaunchAgent example templates..."
LAUNCHAGENTS_DIR="${HERMES_HOME}/usage/launchagents"
if [ -d "${SCRIPT_DIR}/templates/launchagents" ]; then
    mkdir -p "${LAUNCHAGENTS_DIR}"
    cp -f "${SCRIPT_DIR}/templates/launchagents/"*.plist "${LAUNCHAGENTS_DIR}/" 2>/dev/null || true
    cp -f "${SCRIPT_DIR}/templates/launchagents/README.md" "${LAUNCHAGENTS_DIR}/" 2>/dev/null || true
fi
echo "       → ${LAUNCHAGENTS_DIR} (examples only; generate real plists with: usagectl.py autostart --output-dir <dir>)"

# 6. Enable plugin
echo "[7/7] Enabling plugin..."
if command -v hermes &>/dev/null; then
    hermes plugins enable "${PLUGIN_NAME}" 2>/dev/null && echo "       Plugin ${PLUGIN_NAME} enabled" || echo "       ⚠  Could not enable plugin (may need restart)"
else
    echo "       ⚠  'hermes' CLI not found — enable manually: hermes plugins enable ${PLUGIN_NAME}"
fi

echo ""
echo "Done! Usage Monitor installed."
echo ""
echo "Quick test:"
echo "  ${SKILL_DIR}/scripts/usagectl.py status"

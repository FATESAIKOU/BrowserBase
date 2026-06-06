#!/usr/bin/env bash
set -euo pipefail

OP_HOME="${1:?Usage: $0 <opencode_homedir>  (e.g. ~/.config/opencode)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Skill symlink ─────────────────────────────────────────────────────────────

mkdir -p "$OP_HOME/skills"
rm -f "$OP_HOME/skills/browser-profile"
ln -sfn "${SCRIPT_DIR}/skills/browser-profile" "$OP_HOME/skills/browser-profile"
echo "[OK] skill: ${OP_HOME}/skills/browser-profile -> ${SCRIPT_DIR}/skills/browser-profile"

# ── Scaffolding ───────────────────────────────────────────────────────────────

mkdir -p "${SCRIPT_DIR}/screenshots" "${SCRIPT_DIR}/user_profile" "${SCRIPT_DIR}/.opencode"

# ── Project opencode.json (merge with jq, never clobber) ──────────────────────

PYTHON="$(command -v python3 || command -v python)"
CONFIG="${SCRIPT_DIR}/.opencode/opencode.json"

if [ ! -f "$CONFIG" ]; then
  echo '{}' > "$CONFIG"
fi

jq \
  --arg schema "https://opencode.ai/config.json" \
  --arg start_sh "${SCRIPT_DIR}/start_browser.sh" \
  --arg project_dir "${SCRIPT_DIR}" \
  --arg python "$PYTHON" \
  --arg mcp_server "${SCRIPT_DIR}/mcp-servers/cdp-server.py" \
  --arg screenshot_dir "${SCRIPT_DIR}/screenshots" \
  '
  ."$schema" = $schema |
  .permission."cdp_*" = "allow" |
  .permission.bash |= (. // {} | .[$start_sh] = "allow") |
  .permission.external_directory |= (. // {} | .[$project_dir] = "allow") |
  .mcp |= (. // {} | .["cdp-enh"] = {
    type: "local",
    command: [$python, $mcp_server],
    environment: { CDP_PORT: "9222", CDP_SCREENSHOT_DIR: $screenshot_dir },
    enabled: true,
    timeout: 30000
  })
  ' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"

echo "[OK] project config: ${CONFIG}"

echo ""
echo "Done. bash ${SCRIPT_DIR}/start_browser.sh ${SCRIPT_DIR}/user_profile"

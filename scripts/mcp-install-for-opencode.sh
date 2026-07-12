#!/usr/bin/env bash
set -euo pipefail

OP_HOME="${1:?Usage: $0 <opencode_homedir>  (e.g. ~/.config/opencode)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Skill symlink ─────────────────────────────────────────────────────────────

mkdir -p "$OP_HOME/skills"
rm -f "$OP_HOME/skills/browser-profile"
ln -sfn "${PROJECT_DIR}/skills/browser-profile" "$OP_HOME/skills/browser-profile"
echo "[OK] skill: ${OP_HOME}/skills/browser-profile -> ${PROJECT_DIR}/skills/browser-profile"

# ── Scaffolding ───────────────────────────────────────────────────────────────

mkdir -p "${PROJECT_DIR}/screenshots" "${PROJECT_DIR}/user_profile"

# ── Global opencode.json (merge with jq, never clobber) ───────────────────────

PYTHON="$(command -v python3 || command -v python)"
CONFIG="${OP_HOME}/opencode.json"

if [ ! -f "$CONFIG" ]; then
  echo '{}' > "$CONFIG"
fi

jq \
  --arg schema "https://opencode.ai/config.json" \
  --arg start_sh "${PROJECT_DIR}/start_browser.sh" \
  --arg project_dir "${PROJECT_DIR}" \
  --arg python "$PYTHON" \
  --arg mcp_server "${PROJECT_DIR}/mcp-servers/cdp-server.py" \
  --arg screenshot_dir "${PROJECT_DIR}/screenshots" \
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

echo "[OK] global config: ${CONFIG}"

echo ""
echo "Done. bash ${PROJECT_DIR}/start_browser.sh ${PROJECT_DIR}/user_profile"

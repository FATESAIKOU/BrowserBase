#!/usr/bin/env bash
# Install BrowserBase's chrome-devtools MCP + skill into OpenCode.
# Idempotent. Re-run to update the vendored server and skill to the latest release.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

OPENCODE_HOME="${OPENCODE_HOME:-$HOME/.config/opencode}"
CONFIG="${OPENCODE_HOME}/opencode.json"

command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found"; exit 1; }
[ -d "$OPENCODE_HOME" ] || { echo "[ERROR] $OPENCODE_HOME not found"; exit 1; }

update_vendor

echo "[mcp] writing ${CONFIG} ..."
[ -f "$CONFIG" ] || echo '{}' > "$CONFIG"
jq \
  --arg schema "https://opencode.ai/config.json" \
  --arg wrapper "$WRAPPER" \
  --arg project_dir "$PROJECT_DIR" \
  '
  ."$schema" = $schema |
  .permission.external_directory |= ((. // {}) | .[$project_dir] = "allow") |
  .permission |= del(.["cdp_*"]) |
  .permission.bash |= ((. // {}) | with_entries(select(.key | endswith("start_browser.sh") | not))) |
  .mcp |= ((. // {}) | del(.["cdp-enh"]) | .["chrome-devtools"] = {
    type: "local",
    command: [$wrapper],
    enabled: true,
    timeout: 60000
  })
  ' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"

# OpenCode also loaded a hand-written skill previously; drop it, use the official one.
rm -f "${OPENCODE_HOME}/skills/browser-profile"
link_skill "${OPENCODE_HOME}/skills"

echo "Done. Restart OpenCode to pick up the server and skill."

#!/usr/bin/env bash
# Vendor BrowserBase's chrome-devtools MCP for OpenCode.
# Idempotent. Re-run to update the vendored server to the latest release.
#
# Nothing is registered globally: use scripts/browser-opencode.sh [dir] to add the MCP + skill to
# one project directory. This script also removes the global entries older versions wrote, and
# touches only those leftovers (see below); update, other permissions and other MCP servers stay.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

OPENCODE_HOME="${OPENCODE_HOME:-$HOME/.config/opencode}"
CONFIG="${OPENCODE_HOME}/opencode.json"
trap 'rm -f "${CONFIG}.tmp"' EXIT

command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found"; exit 1; }

update_vendor

# Clean up what older versions wrote. Only our own leftovers are touched: update, every other
# permission and every other mcp entry stay as they are. permission.external_directory[<this repo>]
# (also written by older versions) is deliberately left in place: it is indistinguishable from a
# user's rule and may be what lets an agent read the linked skill.
if [ -f "$CONFIG" ]; then
  jq --arg w "$WRAPPER" '
    (if (.permission | type) == "object" then .permission |= del(.["cdp_*"]) else . end)
    | (if (.permission.bash | type) == "object" then .permission.bash |= with_entries(select(.key | endswith("start_browser.sh") | not)) else . end)
    | (if (.mcp | type) == "object" then .mcp |= (del(.["cdp-enh"]) | if .["chrome-devtools"].command[0]? == $w then del(.["chrome-devtools"]) else . end) else . end)
  ' "$CONFIG" > "${CONFIG}.tmp"
  if [ "$(jq -S . "$CONFIG")" != "$(jq -S . "${CONFIG}.tmp")" ]; then
    mv "${CONFIG}.tmp" "$CONFIG"; echo "[mcp] removed old OpenCode leftovers from ${CONFIG}"
  else
    rm -f "${CONFIG}.tmp"
  fi
fi

unlink_skill "${OPENCODE_HOME}/skills"

echo "Done. Add the browser to one project with: ${SCRIPT_DIR}/browser-opencode.sh [dir]"

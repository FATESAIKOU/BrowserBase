#!/usr/bin/env bash
# Vendor BrowserBase's chrome-devtools MCP for OpenCode.
# Idempotent. Re-run to update the vendored server to the latest release.
#
# Nothing is registered globally: use scripts/browser-opencode.sh [dir] to add the MCP + skill to
# one project directory. This script also removes the global entries older versions wrote, and
# touches nothing else in the global config (permissions, update, other MCP servers stay).
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

OPENCODE_HOME="${OPENCODE_HOME:-$HOME/.config/opencode}"
CONFIG="${OPENCODE_HOME}/opencode.json"

command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found"; exit 1; }

update_vendor

if [ -f "$CONFIG" ] && jq -e --arg w "$WRAPPER" '.mcp["chrome-devtools"].command[0]? == $w' "$CONFIG" >/dev/null 2>&1; then
  jq 'del(.mcp["chrome-devtools"])' "$CONFIG" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"
  echo "[mcp] removed old global 'chrome-devtools' from ${CONFIG}"
fi

unlink_skill "${OPENCODE_HOME}/skills"

echo "Done. Add the browser to one project with: ${SCRIPT_DIR}/browser-opencode.sh [dir]"

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

command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found"; exit 1; }

update_vendor

# Skill link first, so a config problem below cannot leave it behind.
unlink_skill "${OPENCODE_HOME}/skills"

# Clean up what older versions of THIS installer wrote (names we chose ourselves: mcp "cdp-enh",
# permission "cdp_*", bash rules ending in start_browser.sh, and our chrome-devtools entry).
# update, every other permission and every other mcp entry stay as they are.
# permission.external_directory[<this repo>] (also written by older versions) is deliberately left:
# it is indistinguishable from a user's rule and may be what lets an agent read the linked skill.
RC=0
if [ -f "$CONFIG" ]; then
  TMP="$(mktemp "${CONFIG}.XXXXXX")"
  trap 'rm -f "$TMP"' EXIT
  if jq --arg w "$WRAPPER" '
    (if (.permission | type) == "object" then .permission |= del(.["cdp_*"]) else . end)
    | (if (.permission.bash | type) == "object" then .permission.bash |= with_entries(select(.key | endswith("start_browser.sh") | not)) else . end)
    | (if (.mcp | type) == "object" then .mcp |= (del(.["cdp-enh"])
        | if ((.["chrome-devtools"].command? | if type == "array" then .[0] else . end) == $w) then del(.["chrome-devtools"]) else . end) else . end)
  ' "$CONFIG" > "$TMP" 2>/dev/null; then
    if [ "$(jq -S . "$CONFIG")" != "$(jq -S . "$TMP")" ]; then
      chmod "$(stat -f %Lp "$CONFIG" 2>/dev/null || stat -c %a "$CONFIG")" "$TMP"
      mv "$TMP" "$CONFIG"; echo "[mcp] removed old OpenCode leftovers from ${CONFIG}"
    fi
  else
    echo "[WARN] could not clean ${CONFIG} (not plain JSON, or unexpected shape); left unchanged." >&2
    RC=1
  fi
fi

if [ "$RC" = 0 ]; then echo "Done. Add the browser to one project with: ${SCRIPT_DIR}/browser-opencode.sh [dir]"; fi
exit "$RC"

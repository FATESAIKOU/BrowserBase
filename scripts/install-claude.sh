#!/usr/bin/env bash
# Vendor BrowserBase's chrome-devtools MCP for Claude Code.
# Idempotent. Re-run to update the vendored server to the latest release.
#
# Nothing is registered globally: the MCP's tool definitions (and the skill description) would
# sit in every session's context. Start browser sessions with scripts/browser-claude.sh instead.
# This script also cleans up what older versions installed globally.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

command -v claude >/dev/null 2>&1 || { echo "[ERROR] 'claude' not on PATH"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found"; exit 1; }

update_vendor

# Remove the user-scope 'chrome-devtools' MCP only if it points at our wrapper.
# (Read the config directly: `claude mcp get` would health-check, i.e. start, the server.)
CLAUDE_JSON="${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json"
if [ -f "$CLAUDE_JSON" ] && jq -e --arg w "$WRAPPER" '.mcpServers["chrome-devtools"].command == $w' "$CLAUDE_JSON" >/dev/null 2>&1; then
  claude mcp remove -s user "chrome-devtools" >/dev/null && echo "[mcp] removed old global 'chrome-devtools' registration"
fi

unlink_skill "${HOME}/.claude/skills"

echo "Done. Start browser sessions with: ${SCRIPT_DIR}/browser-claude.sh"

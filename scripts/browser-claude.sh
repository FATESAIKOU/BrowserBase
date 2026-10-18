#!/usr/bin/env bash
# Start a Claude Code session WITH the BrowserBase chrome-devtools MCP attached.
#
# The MCP is deliberately not installed globally (see install-claude.sh): its tool list costs
# context in every session, so only sessions that need a browser opt in via this launcher.
# Extra arguments are passed straight to `claude`.
#
# Not --strict-mcp-config: that would also hide the user's other MCP servers. Without it,
# --mcp-config only ADDS this server to whatever the session already has.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="${SCRIPT_DIR}/chrome-devtools-wrapper.sh"

command -v claude >/dev/null 2>&1 || { echo "[ERROR] 'claude' not on PATH" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "[ERROR] 'node' not on PATH" >&2; exit 1; }

# --mcp-config takes inline JSON, so no config file lives in the repo with a hard-coded path.
CONFIG="$(node -e 'console.log(JSON.stringify({mcpServers:{"chrome-devtools":{command:process.argv[1],args:[]}}}))' "$WRAPPER")"

exec claude --mcp-config "$CONFIG" "$@"

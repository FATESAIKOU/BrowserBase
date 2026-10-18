#!/usr/bin/env bash
# Start a Claude Code session WITH the BrowserBase chrome-devtools MCP attached.
#
# The MCP is deliberately not installed globally (see install-claude.sh): its tool list costs
# context in every session, so only sessions that need a browser opt in via this launcher.
# Extra arguments are passed straight to `claude` (session launcher only: not for subcommands).
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

# --mcp-config is variadic (<configs...>): anything after it that is not an option, e.g. a bare
# prompt, would be read as another config. So it goes AFTER the user's arguments (an option token
# ends any variadic list before it), but before a literal `--` if the caller passed one.
before=(); after=(); seen=0
for a in "$@"; do
  [ "$seen" = 0 ] && [ "$a" = "--" ] && seen=1
  if [ "$seen" = 1 ]; then after+=("$a"); else before+=("$a"); fi
done
exec claude ${before[@]+"${before[@]}"} --mcp-config "$CONFIG" ${after[@]+"${after[@]}"}

#!/usr/bin/env bash
# Install BrowserBase's chrome-devtools MCP + skill into Claude Code (user scope).
# Idempotent. Re-run to update the vendored server and skill to the latest release.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

command -v claude >/dev/null 2>&1 || { echo "[ERROR] 'claude' not on PATH"; exit 1; }

update_vendor

echo "[mcp] registering 'chrome-devtools' (user scope) ..."
claude mcp remove -s local "chrome-devtools" >/dev/null 2>&1 || true
claude mcp remove -s user  "chrome-devtools" >/dev/null 2>&1 || true
claude mcp add    -s user  "chrome-devtools" "$WRAPPER"

link_skill "${HOME}/.claude/skills"

echo "Done. Restart Claude Code to pick up the server and skill."

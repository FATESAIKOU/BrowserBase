#!/usr/bin/env bash
# Vendor BrowserBase's chrome-devtools MCP + skill for Claude Code.
# Idempotent. Re-run to update the vendored server and skill to the latest release.
#
# The MCP server is NOT registered globally any more: its 29 tool definitions would sit in every
# session's context. Start browser sessions with scripts/browser-claude.sh instead. This script
# also removes the global registration older versions wrote.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

command -v claude >/dev/null 2>&1 || { echo "[ERROR] 'claude' not on PATH"; exit 1; }

update_vendor

# Remove only a 'chrome-devtools' entry that points at our wrapper; leave everything else alone.
for scope in user local; do
  if claude mcp get "chrome-devtools" 2>/dev/null | grep -q "chrome-devtools-wrapper.sh"; then
    claude mcp remove -s "$scope" "chrome-devtools" >/dev/null 2>&1 && echo "[mcp] removed old '${scope}'-scope 'chrome-devtools' registration" || true
  fi
done

link_skill "${HOME}/.claude/skills"

echo "Done. Start browser sessions with: ${SCRIPT_DIR}/browser-claude.sh"

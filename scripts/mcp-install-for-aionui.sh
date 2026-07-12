#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AIONCORE="${AIONUI_HELPER_BIN:-/Applications/AionUi.app/Contents/Resources/bundled-aioncore/darwin-arm64/aioncore}"

if [ ! -x "$AIONCORE" ]; then
  echo "[ERROR] aioncore not found at: $AIONCORE"
  echo "  Set AIONUI_HELPER_BIN or install AionUi first."
  exit 1
fi

PYTHON="$(command -v python3 || command -v python)"
CDP_SERVER="${PROJECT_DIR}/mcp-servers/cdp-server.py"
SCREENSHOT_DIR="${PROJECT_DIR}/screenshots"

if [ ! -f "$CDP_SERVER" ]; then
  echo "[ERROR] cdp-server.py not found at: $CDP_SERVER"
  exit 1
fi

mkdir -p "$SCREENSHOT_DIR"

echo "=== BrowserBase MCP installer for AionUi ==="
echo "  Project:   $PROJECT_DIR"
echo "  Python:    $PYTHON"
echo "  CDP port:  9222"
echo "  Screenshots: $SCREENSHOT_DIR"
echo ""

# ── Create MCP server ──────────────────────────────────────────────────────────

echo "[1/2] Registering MCP server 'browserbase-cdp' ..."

"$AIONCORE" config mcp servers create <<JSON
{
  "name": "browserbase-cdp",
  "transport": {
    "type": "stdio",
    "command": "$PYTHON",
    "args": ["$CDP_SERVER"],
    "env": {
      "CDP_PORT": "9222",
      "CDP_SCREENSHOT_DIR": "$SCREENSHOT_DIR"
    }
  }
}
JSON

echo ""

# ── Enable the server ─────────────────────────────────────────────────────────

echo "[2/2] Enabling MCP server ..."

SERVER_ID=$("$AIONCORE" config mcp servers list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('data', []):
    if s.get('name') == 'browserbase-cdp':
        print(s['id'])
        break
")

if [ -n "$SERVER_ID" ]; then
  "$AIONCORE" config mcp servers toggle <<JSON
{
  "server_id": "$SERVER_ID"
}
JSON
else
  echo "[WARN] Could not find server ID to enable. Enable manually in AionUi settings."
fi

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Start the browser:  bash ${PROJECT_DIR}/start_browser.sh ${PROJECT_DIR}/user_profile"
echo "  2. Restart AionUi or reload MCP servers"
echo "  3. Tools will appear as: cdp_navigate, cdp_screenshot, cdp_click_xy, ..."
echo ""
echo "To verify the browser is running:"
echo "  curl -s http://localhost:9222/json/version"

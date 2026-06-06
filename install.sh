#!/usr/bin/env bash
# install.sh — one-shot install & verify for BrowserBase on a new machine
#
# Usage:
#   bash install.sh
#
# The script:
#   1.  Auto-detects Python 3.11+ / Chrome / Chromium / Brave /
#       Edge (macOS & Linux).
#   2.  Installs Python deps (mcp, httpx, websockets) into the user site.
#   3.  Writes .opencode/opencode.json  (project-scoped MCP).
#   4.  Symlinks the browser-profile skill into ~/.config/opencode/skills/.
#   5.  Optionally disables an old global "cdp" MCP to avoid
#       tool-name collisions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SCRIPT_DIR

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/browser.sh
source "${SCRIPT_DIR}/lib/browser.sh"

OPENCODE_CONFIG="${HOME}/.config/opencode/opencode.json"
SKILLS_DIR="${HOME}/.config/opencode/skills"
MCP_SERVER="${SCRIPT_DIR}/mcp-servers/cdp-server.py"
SCREENSHOT_DIR="${SCRIPT_DIR}/screenshots"
PROFILE_DIR="${SCRIPT_DIR}/user_profile"

banner "BrowserBase — Install"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────

echo "── Detecting environment ($(os_name))"
echo ""

# ---- Python ----
echo "── Python"
PYTHON=""
for candidate in python3 python python3.12 python3.11; do
  found="$(command -v "$candidate" 2>/dev/null || true)"
  if [[ -z "$found" ]]; then continue; fi
  ver="$("$found" --version 2>&1 || true)"
  if echo "$ver" | grep -qE '3\.(1[1-9]|[2-9][0-9])'; then
    PYTHON="$found"
    break
  fi
done
[[ -n "$PYTHON" ]] || die "Python 3.11+ not found.  Install via brew / apt / asdf."
log_ok "$PYTHON ($($PYTHON --version))"

# ---- Python packages ----
echo -n "  Checking mcp, httpx, websockets ... "
if "$PYTHON" -c "import mcp, httpx, websockets" 2>/dev/null; then
  echo -e "${GREEN}ok${NC}"
else
  echo ""
  log_warn "Missing Python packages. Installing ..."
  "$PYTHON" -m pip install --quiet mcp httpx websockets
  if "$PYTHON" -c "import mcp, httpx, websockets" 2>/dev/null; then
    log_ok "Packages installed."
  else
    die "Could not install packages. Run manually:\n         $PYTHON -m pip install mcp httpx websockets"
  fi
fi

# ---- Browser ----
echo "── Browser"
BROWSER="$(detect_browser)"
log_ok "$(basename "$BROWSER"): $BROWSER"

# ── 2. Directory scaffolding ──────────────────────────────────────────────────

echo ""
echo "── Directory scaffolding"
mkdir -p "${SCRIPT_DIR}/.opencode"
mkdir -p "${SCREENSHOT_DIR}"
mkdir -p "${PROFILE_DIR}"
log_ok "Created: .opencode/ screenshots/ user_profile/"

# ── 3. Project-level opencode config ──────────────────────────────────────────

echo ""
echo "── Writing .opencode/opencode.json"

cat > "${SCRIPT_DIR}/.opencode/opencode.json" <<JSONEOF
{
  "\$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": {
      "*/browserbase/*": "allow",
      "*/BrowserBase/*": "allow",
      "${SCRIPT_DIR}/*": "allow"
    },
    "external_directory": {
      "${SCRIPT_DIR}": "allow",
      "${PROFILE_DIR}": "allow",
      "${SCREENSHOT_DIR}": "allow"
    },
    "cdp_*": "allow"
  },
  "mcp": {
    "cdp-enh": {
      "type": "local",
      "command": [
        "${PYTHON}",
        "${MCP_SERVER}"
      ],
      "environment": {
        "CDP_PORT": "9222",
        "CDP_SCREENSHOT_DIR": "${SCREENSHOT_DIR}"
      },
      "enabled": true,
      "timeout": 30000
    }
  }
}
JSONEOF

log_ok ".opencode/opencode.json"

# ── 4. Skill symlink ──────────────────────────────────────────────────────────

echo ""
echo "── Installing browser-profile skill"

mkdir -p "${SKILLS_DIR}"
rm -f "${SKILLS_DIR}/browser-profile"
ln -sfn "${SCRIPT_DIR}/skills/browser-profile" "${SKILLS_DIR}/browser-profile"
log_ok "Skill: ${SKILLS_DIR}/browser-profile -> ${SCRIPT_DIR}/skills/browser-profile"

# ── 5. Optional: disable old global "cdp" MCP ─────────────────────────────────

echo ""
echo "── Checking for old MCP conflict"

if [[ -f "$OPENCODE_CONFIG" ]] && "$PYTHON" -c "
import json; cfg=json.load(open('${OPENCODE_CONFIG}'))
m=cfg.get('mcp',{}); exit(0 if 'cdp' in m and m['cdp'].get('enabled',True) else 1)
" 2>/dev/null; then
  log_warn "Old global 'cdp' MCP found — tool names overlap with cdp-enh."
  echo ""
  echo "    1) Disable old MCP (recommended — cdp-enh is a superset)"
  echo "    2) Keep both        (tools prefixed: cdp_ vs cdp-enh_)"
  echo ""
  read -r -p "    Choice [1/2] (default=1): " choice </dev/tty
  choice="${choice:-1}"
  if [[ "$choice" == "1" ]]; then
    "$PYTHON" -c "
import json; p='${OPENCODE_CONFIG}'; cfg=json.load(open(p))
cfg.setdefault('mcp',{})['cdp']['enabled']=False
json.dump(cfg, open(p,'w'), indent=2); open(p,'a').write('\n')
"
    log_ok "Old 'cdp' MCP disabled."
  else
    log_info "Both MCPs kept. Tools will be prefixed: cdp-enh_*"
  fi
else
  log_ok "No conflict detected."
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────

echo ""
banner "Installation complete"
echo "  Project:   ${SCRIPT_DIR}"
echo "  Python:    ${PYTHON}"
echo "  Browser:   $(basename "$BROWSER")"
echo "  CDP port:  9222"
echo ""
echo "  Next steps:"
echo "    1. Start the browser:"
echo "         bash ${SCRIPT_DIR}/start_browser.sh"
echo "    2. Log into the sites you need in the opened Chrome window."
echo "    3. Use opencode in this project:"
echo "         cd ${SCRIPT_DIR} && opencode"
echo ""
echo "  Tips:"
echo "    - HEADLESS=1 bash start_browser.sh   (no GUI)"
echo "    - CHROME_ARGS='--window-size=1920,1080' bash start_browser.sh"
echo "    - CDP_PORT=9333 bash start_browser.sh  (alt port)"
echo ""

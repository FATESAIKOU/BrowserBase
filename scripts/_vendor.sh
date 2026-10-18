# Shared helper: pull the latest official chrome-devtools-mcp into <project>/.mcp,
# and expose paths used by the per-client installers. Sourced, not executed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WRAPPER="${SCRIPT_DIR}/chrome-devtools-wrapper.sh"
VENDOR_DIR="${PROJECT_DIR}/.mcp"
# The chrome-devtools skill shipped inside the package — the browser-automation one.
SKILL_SRC="${BROWSERBASE_SKILL_SRC:-${VENDOR_DIR}/node_modules/chrome-devtools-mcp/skills/chrome-devtools}"

update_vendor() {
  echo "[vendor] installing latest chrome-devtools-mcp into ${VENDOR_DIR} ..."
  npm install --prefix "$VENDOR_DIR" chrome-devtools-mcp@latest >/dev/null
  chmod +x "$WRAPPER"
  mkdir -p "${PROJECT_DIR}/user_profile"
  local v
  v="$(node -p "require('${VENDOR_DIR}/node_modules/chrome-devtools-mcp/package.json').version" 2>/dev/null || echo '?')"
  echo "[vendor] chrome-devtools-mcp ${v} ready"
}

# Remove <skills_dir>/chrome-devtools only if it is a symlink to our SKILL_SRC; anything else stays.
unlink_skill() {   # unlink_skill <client_skills_dir>
  local link="$1/chrome-devtools"
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$SKILL_SRC" ]; then
    rm "$link" && echo "[skill] removed old global link ${link}"
  fi
}

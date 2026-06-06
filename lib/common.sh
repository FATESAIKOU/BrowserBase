#!/usr/bin/env bash
# common.sh — shared utilities for BrowserBase scripts
# Source this file; do not execute directly.

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Logging ───────────────────────────────────────────────────────────────────

log_info()  { echo -e "  ${CYAN}[INFO]${NC}  $*"; }
log_ok()    { echo -e "  ${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "  ${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "  ${RED}[ERROR]${NC} $*"; }

die()       { log_err "$*"; exit 1; }

banner() {
  local text="$*"
  local width=48
  echo ""
  echo -e "${GREEN}$(printf '╔═%*s═╗' "$width" '' | tr ' ' '═')${NC}"
  echo -e "${GREEN}║  $(printf '%-*s' "$width" "$text")║${NC}"
  echo -e "${GREEN}$(printf '╚═%*s═╝' "$width" '' | tr ' ' '═')${NC}"
  echo ""
}

# ── OS detection ──────────────────────────────────────────────────────────────

is_macos()  { [[ "$(uname -s)" == "Darwin" ]]; }
is_linux()  { [[ "$(uname -s)" == "Linux" ]]; }

os_name() {
  if is_macos; then echo "macOS"; elif is_linux; then echo "Linux"; else printf '%s' "$(uname -s)"; fi
}

# ── Portable helpers ──────────────────────────────────────────────────────────

# Resolve the BrowserBase project root (where this file or a caller lives).
# Use the first non-symlink ancestor that contains start_browser.sh.
project_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/start_browser.sh" ]]; then
      echo "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  # Fallback: same directory as the calling script
  cd "$(dirname "${BASH_SOURCE[1]}")" && pwd
}

# Ensure curl is available.  Most systems have it; fail early otherwise.
require_curl() {
  command -v curl &>/dev/null || die "curl is required but not found."
}

#!/usr/bin/env bash
# browser.sh — cross-platform browser detection, launch, and CDP readiness
# Source this file; do not execute directly.

set -euo pipefail

# Determine the directory where this library lives.
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Source common helpers (colours, logging, OS helpers)
# shellcheck source=lib/common.sh
source "${_LIB_DIR}/common.sh"

# ── Public interface (expected env vars) ──────────────────────────────────────
#   CHROME        – override browser executable path
#   CHROME_ARGS   – extra flags appended to launch command (space-separated)
#   HEADLESS      – if non-empty, add --headless=new
#   CDP_PORT      – remote-debugging-port (default 9222)
#   PROFILE_DIR   – --user-data-dir  (default PROJECT_ROOT/user_profile)

export _BROWSER_LAUNCH_PID=""

# ── Browser executable resolution ─────────────────────────────────────────────

detect_browser() {
  local override="${CHROME:-}"

  # 1. Explicit override always wins
  if [[ -n "$override" ]]; then
    if [[ -x "$override" ]]; then
      printf '%s' "$override"
      return 0
    fi
    die "CHROME is set to '$override' but it is not executable."
  fi

  # 2. Per-browser env vars
  local candidates=()

  if is_macos; then
    candidates=(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      "/Applications/Chromium.app/Contents/MacOS/Chromium"
      "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    )
  elif is_linux; then
    candidates=(
      "/opt/google/chrome/chrome"
      "/opt/google/chrome/google-chrome"
      "/opt/brave.com/brave/brave"
      "/opt/brave.com/brave/brave-browser"
      "/opt/microsoft/msedge/msedge"
      "/usr/bin/google-chrome"
      "/usr/bin/google-chrome-stable"
      "/usr/bin/chromium"
      "/usr/bin/chromium-browser"
      "/usr/bin/brave-browser"
      "/usr/bin/microsoft-edge"
    )
  fi

  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]]; then
      printf '%s' "$c"
      return 0
    fi
  done

  # 3. PATH fallback (also catches Snap / Flatpak / Nix)
  for name in google-chrome google-chrome-stable chromium chromium-browser brave-browser microsoft-edge; do
    local found
    found="$(command -v "$name" 2>/dev/null || true)"
    if [[ -n "$found" && -x "$found" ]]; then
      printf '%s' "$found"
      return 0
    fi
  done

  die "No supported browser found. Set CHROME=/path/to/browser and retry.\n       Supported: Google Chrome, Chromium, Brave, Microsoft Edge"
}

# ── Launch ─────────────────────────────────────────────────────────────────────

launch_browser() {
  local browser profile_dir port extra_args
  browser="$1";    profile_dir="$2";    port="${3:-9222}";    extra_args="${4:-}"

  local args=(
    "$browser"
    "--remote-debugging-port=$port"
    "--user-data-dir=$profile_dir"
    "--no-first-run"
    "--no-default-browser-check"
    "--disable-background-networking"
    "--disable-sync"
    "--disable-extensions-file-access-check"
    "--disable-component-update"
  )

  # Optional headless mode
  if [[ -n "${HEADLESS:-}" ]]; then
    args+=("--headless=new")
  fi

  # User-supplied extra args (allow space-separated, splitting via eval is safe
  # because extra_args is already a string, and we handle quoting in the caller)
  if [[ -n "$extra_args" ]]; then
    # Use eval to split the string into separate words properly
    # This handles quoted arguments like --lang="en-US"
    eval "args+=($extra_args)"
  fi

  echo "Browser:    $browser"
  echo "Profile:    $profile_dir"
  echo "CDP port:   $port"
  if [[ -n "${HEADLESS:-}" ]]; then echo "Headless:   yes"; fi
  if [[ -n "$extra_args" ]]; then echo "Extra args: $extra_args"; fi
  echo ""

  "${args[@]}" >/dev/null 2>&1 &
  _BROWSER_LAUNCH_PID=$!

  # Give the process a moment to start before polling CDP
  sleep 0.5
}

# ── CDP readiness ─────────────────────────────────────────────────────────────

is_cdp_alive() {
  local port="${1:-9222}"
  curl -s "http://localhost:${port}/json/version" >/dev/null 2>&1
}

wait_for_cdp() {
  local port="${1:-9222}" timeout="${2:-15}" elapsed=0

  while (( elapsed < timeout )); do
    if is_cdp_alive "$port"; then
      log_ok "CDP endpoint ready on http://localhost:${port}"
      return 0
    fi
    sleep 1
    ((elapsed++))
  done

  log_warn "CDP endpoint not responding after ${timeout}s"
  if [[ -n "${_BROWSER_LAUNCH_PID:-}" ]]; then
    log_warn "Browser PID ${_BROWSER_LAUNCH_PID} may still be starting"
  fi
  return 1
}

# ── Composite: detect + launch + wait ─────────────────────────────────────────

start_browser() {
  local profile_dir="${1:-${PROJECT_ROOT:-.}/user_profile}"
  local port="${2:-9222}"
  local extra_args="${3:-${CHROME_ARGS:-}}"

  require_curl

  local browser
  browser="$(detect_browser)"
  launch_browser "$browser" "$profile_dir" "$port" "$extra_args"
  wait_for_cdp "$port" 15
}

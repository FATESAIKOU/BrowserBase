#!/usr/bin/env bash
# start_browser.sh — launch Chrome with a persistent profile and CDP on port 9222
#
# Environment variables you can override:
#   CHROME        path to browser executable (auto-detected if not set)
#   CHROME_ARGS   extra flags appended to launch (e.g. --window-size=1920,1080 --lang=en)
#   HEADLESS      set to any value to add --headless=new
#   CDP_PORT      remote-debugging-port (default 9222)
#   PROFILE_DIR   --user-data-dir (default: ./user_profile)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
export PROJECT_ROOT

# shellcheck source=lib/common.sh
source "${PROJECT_ROOT}/lib/common.sh"
# shellcheck source=lib/browser.sh
source "${PROJECT_ROOT}/lib/browser.sh"

start_browser \
  "${PROFILE_DIR:-${PROJECT_ROOT}/user_profile}" \
  "${CDP_PORT:-9222}" \
  "${CHROME_ARGS:-}"

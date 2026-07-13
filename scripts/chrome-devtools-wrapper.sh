#!/usr/bin/env bash
# BrowserBase: run the OFFICIAL chrome-devtools-mcp against a real, logged-in Chrome.
#
# Why a wrapper at all: chrome-devtools-mcp can launch its own Chrome, but only through
# Puppeteer. A Puppeteer-launched Chrome is flagged by Google as an insecure/automated
# browser, and any Google session in the profile is dropped to the logged-out state
# (verified: even with --enable-automation removed and navigator.webdriver forced false,
# Google still redirects to the signed-out page). Launching stock Chrome the normal way
# and attaching over --browserUrl does not trip that detection.
#
# Responsibilities:
#   1. Launch stock Chrome, headful, with the persistent BrowserBase profile.
#   2. exec the vendored chrome-devtools-mcp, attached to that Chrome.
# (Skill loading is an install-time concern; see scripts/install-*.sh.)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PROFILE_DIR="${BROWSERBASE_PROFILE:-${PROJECT_DIR}/user_profile}"
CDP_PORT="${BROWSERBASE_CDP_PORT:-9222}"
CHROME="${BROWSERBASE_CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
MCP_BIN="${BROWSERBASE_MCP_BIN:-${PROJECT_DIR}/.mcp/node_modules/.bin/chrome-devtools-mcp}"

cdp_up() { curl -sf --max-time 1 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; }

if ! cdp_up; then
  "$CHROME" \
    --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --hide-crash-restore-bubble \
    about:blank \
    >/dev/null 2>&1 &

  for _ in $(seq 1 40); do
    cdp_up && break
    sleep 0.5
  done
fi

cdp_up || { echo "BrowserBase: CDP not reachable on port ${CDP_PORT}" >&2; exit 1; }

exec "$MCP_BIN" \
  --browserUrl "http://127.0.0.1:${CDP_PORT}" \
  --no-usage-statistics

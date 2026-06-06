#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${1:?Usage: $0 <profile_dir> [headless] [cdp_port] [chrome_path]}"
HEADLESS="${2:-false}"
CDP_PORT="${3:-9222}"
CHROME="${4:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

if [ ! -f "$CHROME" ]; then
  echo "[ERROR] Chrome not found at: $CHROME"
  exit 1
fi

HEADLESS_FLAG=""
if [ "$HEADLESS" != "false" ] && [ -n "$HEADLESS" ]; then
  HEADLESS_FLAG="--headless=new"
fi

echo "Profile:   $PROFILE_DIR"
echo "CDP port:  $CDP_PORT"
echo "Headless:  $HEADLESS"
echo "Chrome:    $CHROME"

"$CHROME" \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-networking \
  --disable-sync \
  $HEADLESS_FLAG \
  > /dev/null 2>&1 &

for i in $(seq 1 15); do
  if curl -s "http://localhost:${CDP_PORT}/json/version" > /dev/null 2>&1; then
    echo "[OK] CDP ready at http://localhost:${CDP_PORT}"
    exit 0
  fi
  sleep 1
done

echo "[WARN] CDP not responding after 15s"
exit 1

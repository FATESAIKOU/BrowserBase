#!/usr/bin/env bash
# Start OpenCode in a directory WITH the BrowserBase chrome-devtools MCP + skill.
#
#   browser-opencode.sh [dir]            add to <dir>/opencode.json + <dir>/.opencode/skills, run opencode
#   browser-opencode.sh --remove [dir]   undo exactly what we added
#
# Only the mcp["chrome-devtools"] key is merged into an existing opencode.json; everything else is
# kept. Inside a git repo/worktree the files we add are put in .git info/exclude (never committed),
# and a *tracked* opencode.json is refused. Env: OPENCODE_BIN overrides the command exec'd
# (default: opencode); BROWSERBASE_SKILL_SRC overrides the vendored skill path.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/_vendor.sh"

REMOVE=0; DIR=""
for a in "$@"; do
  case "$a" in --remove) REMOVE=1 ;; -*) echo "unknown option: $a" >&2; exit 2 ;; *) DIR="$a" ;; esac
done
DIR="$(cd "${DIR:-$PWD}" && pwd)"
command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found" >&2; exit 1; }

CONFIG="$DIR/opencode.json"
SKILLS="$DIR/.opencode/skills"
LINK="$SKILLS/chrome-devtools"
STATE="$DIR/.opencode/.browserbase-state"   # records what WE created, so --remove can undo exactly that

# --- git bookkeeping -------------------------------------------------------------------------
IN_GIT=0; EXCLUDE=""; PREFIX=""
if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  IN_GIT=1
  PREFIX="$(git -C "$DIR" rev-parse --show-prefix)"           # "" at worktree root, "sub/" below it
  EXCLUDE="$(git -C "$DIR" rev-parse --git-path info/exclude)" # resolves correctly for worktrees
  case "$EXCLUDE" in /*) ;; *) EXCLUDE="$DIR/$EXCLUDE" ;; esac
fi
EXCLUDE_LINES=("/${PREFIX}opencode.json" "/${PREFIX}.opencode/skills/chrome-devtools" "/${PREFIX}.opencode/.browserbase-state")

state_has() { [ -f "$STATE" ] && grep -qxF "$1" "$STATE"; }
state_add() { mkdir -p "$(dirname "$STATE")"; state_has "$1" || echo "$1" >> "$STATE"; }
write_json() { jq "$@" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"; }

if [ "$REMOVE" = 0 ]; then
  if [ "$IN_GIT" = 1 ] && git -C "$DIR" ls-files --error-unmatch opencode.json >/dev/null 2>&1; then
    echo "[ERROR] $CONFIG is tracked by git; refusing to modify a tracked file. Use an untracked directory/worktree." >&2
    exit 1
  fi
  if [ -e "$LINK" ] || [ -L "$LINK" ]; then
    [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$SKILL_SRC" ] || { echo "[ERROR] $LINK exists and is not ours" >&2; exit 1; }
  fi
  if [ -f "$CONFIG" ]; then
    jq -e 'type == "object"' "$CONFIG" >/dev/null || { echo "[ERROR] $CONFIG is not a JSON object" >&2; exit 1; }
    if jq -e --arg w "$WRAPPER" '.mcp["chrome-devtools"] != null and .mcp["chrome-devtools"].command != [$w]' "$CONFIG" >/dev/null; then
      echo "[ERROR] $CONFIG already has a different mcp.chrome-devtools; not overwriting" >&2; exit 1
    fi
  fi
  [ -d "$SKILL_SRC" ] || { echo "[ERROR] vendored skill missing: $SKILL_SRC (run install-claude.sh)" >&2; exit 1; }

  mkdir -p "$SKILLS"
  if [ ! -f "$CONFIG" ]; then echo '{}' > "$CONFIG"; state_add created-config; fi
  jq -e '.mcp == null' "$CONFIG" >/dev/null && state_add created-mcp
  write_json --arg w "$WRAPPER" '.mcp = ((.mcp // {}) + {"chrome-devtools": {type: "local", command: [$w], enabled: true, timeout: 60000}})' "$CONFIG"
  ln -sfn "$SKILL_SRC" "$LINK"

  if [ "$IN_GIT" = 1 ]; then
    mkdir -p "$(dirname "$EXCLUDE")"; touch "$EXCLUDE"
    [ -z "$(tail -c1 "$EXCLUDE")" ] || echo >> "$EXCLUDE"
    for l in "${EXCLUDE_LINES[@]}"; do
      grep -qxF "$l" "$EXCLUDE" || { echo "$l" >> "$EXCLUDE"; state_add "exclude $l"; }
    done
  else
    echo "[note] $DIR is not a git repo; make sure opencode.json and .opencode/ never get committed elsewhere." >&2
  fi
  exec "${OPENCODE_BIN:-opencode}" "$DIR"
fi

# --- remove ----------------------------------------------------------------------------------
if [ -f "$CONFIG" ] && jq -e --arg w "$WRAPPER" '.mcp["chrome-devtools"].command == [$w]' "$CONFIG" >/dev/null 2>&1; then
  write_json 'del(.mcp["chrome-devtools"])' "$CONFIG"
  state_has created-mcp && jq -e '.mcp == {}' "$CONFIG" >/dev/null && write_json 'del(.mcp)' "$CONFIG"
  if state_has created-config && jq -e '. == {}' "$CONFIG" >/dev/null; then rm "$CONFIG"; fi
  echo "[mcp] removed chrome-devtools from $CONFIG"
fi
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$SKILL_SRC" ]; then rm "$LINK"; fi
if [ "$IN_GIT" = 1 ] && [ -f "$EXCLUDE" ] && [ -f "$STATE" ]; then
  for l in "${EXCLUDE_LINES[@]}"; do
    if state_has "exclude $l"; then grep -vxF "$l" "$EXCLUDE" > "${EXCLUDE}.tmp" || true; mv "${EXCLUDE}.tmp" "$EXCLUDE"; fi
  done
fi
rm -f "$STATE"
rmdir "$SKILLS" "$DIR/.opencode" 2>/dev/null || true
echo "[done] removed BrowserBase additions from $DIR"

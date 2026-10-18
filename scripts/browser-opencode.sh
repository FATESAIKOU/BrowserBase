#!/usr/bin/env bash
# Start OpenCode in a directory WITH the BrowserBase chrome-devtools MCP + skill.
#
#   browser-opencode.sh [dir]            add to <dir>/opencode.json + <dir>/.opencode/skills, run opencode
#   browser-opencode.sh --remove [dir]   undo exactly what we added
#
# Only the mcp["chrome-devtools"] key is merged into an existing opencode.json; everything else is
# kept. Inside a git repo/worktree the files we add are put in the repo's info/exclude (never
# committed), and a *tracked* opencode.json is refused. info/exclude is shared by every worktree of
# a repo, so we (a) refuse to hide another worktree's own untracked opencode.json and (b) keep the
# exclude lines until the last worktree using them runs --remove.
# Env: OPENCODE_BIN overrides the command exec'd (default: opencode); BROWSERBASE_SKILL_SRC
# overrides the vendored skill path.
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
trap 'rm -f "${CONFIG}.tmp"' EXIT

# --- git bookkeeping -------------------------------------------------------------------------
esc() { printf '%s' "$1" | sed -e 's/[][\*?]/\\&/g'; }   # gitignore specials: [ ] \ * ?

IN_GIT=0; EXCLUDE=""; PREFIX=""; TOP=""; EXCLUDE_LINES=()
if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  IN_GIT=1
  TOP="$(git -C "$DIR" rev-parse --show-toplevel)"
  PREFIX="$(git -C "$DIR" rev-parse --show-prefix)"            # "" at worktree root, "sub/" below it
  EXCLUDE="$(git -C "$DIR" rev-parse --git-path info/exclude)" # resolves correctly for worktrees
  case "$EXCLUDE" in /*) ;; *) EXCLUDE="$DIR/$EXCLUDE" ;; esac
  P="$(esc "$PREFIX")"   # anchored: full path relative to the worktree root
  EXCLUDE_LINES=("/${P}opencode.json" "/${P}.opencode/skills/chrome-devtools" "/${P}.opencode/.browserbase-state")
fi

other_worktrees() {   # existing worktree paths other than this one
  git -C "$DIR" worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r w; do
    [ "$w" != "$TOP" ] && [ -d "$w" ] && printf '%s\n' "$w"
  done
}
state_has() { [ -f "$STATE" ] && grep -qxF "$1" "$STATE"; }
state_add() { mkdir -p "$(dirname "$STATE")"; state_has "$1" || echo "$1" >> "$STATE"; }

if [ "$REMOVE" = 0 ]; then
  # ---- validate everything before writing anything ----
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
  [ -d "$SKILL_SRC" ] || { echo "[ERROR] vendored skill missing: $SKILL_SRC (run an install script)" >&2; exit 1; }
  if [ "$IN_GIT" = 1 ] && ! { [ -f "$EXCLUDE" ] && grep -qxF "${EXCLUDE_LINES[0]}" "$EXCLUDE"; }; then
    while IFS= read -r w; do
      f="$w/${PREFIX}opencode.json"
      if [ -f "$f" ] && [ ! -f "$w/${PREFIX}.opencode/.browserbase-state" ] \
         && ! git -C "$w" ls-files --error-unmatch -- "${PREFIX}opencode.json" >/dev/null 2>&1; then
        echo "[ERROR] worktree $w has its own untracked ${PREFIX}opencode.json; the shared info/exclude rule would hide it. Aborting." >&2
        exit 1
      fi
    done < <(other_worktrees)
  fi

  # ---- build the new config first: a jq failure leaves nothing behind ----
  CREATED_CONFIG=0; CREATED_MCP=0
  if [ -f "$CONFIG" ]; then INPUT="$(cat "$CONFIG")"; else CREATED_CONFIG=1; INPUT='{}'; fi
  jq -e '.mcp == null' <<<"$INPUT" >/dev/null && CREATED_MCP=1
  printf '%s' "$INPUT" | jq --arg w "$WRAPPER" '.mcp = ((.mcp // {}) + {"chrome-devtools": {type: "local", command: [$w], enabled: true, timeout: 60000}})' > "${CONFIG}.tmp"

  # ---- commit: state + exclude first, then config and symlink ----
  mkdir -p "$SKILLS"
  [ "$CREATED_CONFIG" = 1 ] && state_add created-config
  [ "$CREATED_MCP" = 1 ] && state_add created-mcp
  if [ "$IN_GIT" = 1 ]; then
    mkdir -p "$(dirname "$EXCLUDE")"; touch "$EXCLUDE"
    [ -z "$(tail -c1 "$EXCLUDE")" ] || echo >> "$EXCLUDE"
    for l in "${EXCLUDE_LINES[@]}"; do
      grep -qxF "$l" "$EXCLUDE" || { state_add "exclude $l"; echo "$l" >> "$EXCLUDE"; }
    done
  else
    echo "[note] $DIR is not a git repo; make sure opencode.json and .opencode/ never get committed elsewhere." >&2
  fi
  mv "${CONFIG}.tmp" "$CONFIG"
  ln -sfn "$SKILL_SRC" "$LINK"
  exec "${OPENCODE_BIN:-opencode}" "$DIR"
fi

# --- remove ----------------------------------------------------------------------------------
write_json() { jq "$@" > "${CONFIG}.tmp" && mv "${CONFIG}.tmp" "$CONFIG"; }
if [ -f "$CONFIG" ] && jq -e --arg w "$WRAPPER" '.mcp["chrome-devtools"].command == [$w]' "$CONFIG" >/dev/null 2>&1; then
  write_json 'del(.mcp["chrome-devtools"])' "$CONFIG"
  state_has created-mcp && jq -e '.mcp == {}' "$CONFIG" >/dev/null && write_json 'del(.mcp)' "$CONFIG"
  if state_has created-config && jq -e '. == {} or . == {"mcp": {}}' "$CONFIG" >/dev/null; then rm "$CONFIG"; fi
  echo "[mcp] removed chrome-devtools from $CONFIG"
fi
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$SKILL_SRC" ]; then rm "$LINK"; fi

if [ "$IN_GIT" = 1 ] && [ -f "$EXCLUDE" ] && [ -f "$STATE" ]; then
  HEIR=""   # another worktree that still uses the (shared) exclude lines
  while IFS= read -r w; do
    if [ -f "$w/${PREFIX}.opencode/.browserbase-state" ]; then HEIR="$w/${PREFIX}.opencode/.browserbase-state"; break; fi
  done < <(other_worktrees)
  for l in "${EXCLUDE_LINES[@]}"; do
    state_has "exclude $l" || continue
    if [ -n "$HEIR" ]; then
      grep -qxF "exclude $l" "$HEIR" || echo "exclude $l" >> "$HEIR"   # hand over; last user removes it
    else
      grep -vxF "$l" "$EXCLUDE" > "${EXCLUDE}.tmp" || true; mv "${EXCLUDE}.tmp" "$EXCLUDE"
    fi
  done
  [ -z "$HEIR" ] || echo "[note] another worktree still uses the shared git exclude rules; they stay until it runs --remove."
fi
rm -f "$STATE"
rmdir "$SKILLS" "$DIR/.opencode" 2>/dev/null || true
echo "[done] removed BrowserBase additions from $DIR"

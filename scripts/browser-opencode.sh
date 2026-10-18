#!/usr/bin/env bash
# Start OpenCode in a directory WITH the BrowserBase chrome-devtools MCP + skill.
#
#   browser-opencode.sh [dir]            add to <dir>/opencode.json + <dir>/.opencode/skills, run opencode
#   browser-opencode.sh --remove [dir]   remove the mcp key, the skill link and the state file
#
# Only the mcp["chrome-devtools"] key is merged into an existing opencode.json; everything else is
# kept. Inside a git repo/worktree the paths we add are appended to the repo's info/exclude (shared
# by all its worktrees) so they are never committed; a *tracked* opencode.json is refused.
# --remove never edits info/exclude: those lines are idempotent and only hide the two paths.
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
STATE="$DIR/.opencode/.browserbase-state"   # records only what WE created (created-config / created-mcp)

# Temp file next to the config (same filesystem, so mv is atomic). Only the file we created is
# ever deleted by the trap; a user's own opencode.json.tmp is never touched.
TMP=""
trap '[ -z "$TMP" ] || rm -f "$TMP"' EXIT
new_tmp() {   # new_tmp: creates $TMP with the config's mode (644 for a new file)
  TMP="$(mktemp "${CONFIG}.XXXXXX")"
  local mode=644
  [ ! -f "$CONFIG" ] || mode="$(stat -f %Lp "$CONFIG" 2>/dev/null || stat -c %a "$CONFIG")"
  chmod "$mode" "$TMP"
}
state_has() { [ -f "$STATE" ] && grep -qxF "$1" "$STATE"; }
state_add() { mkdir -p "$(dirname "$STATE")"; state_has "$1" || echo "$1" >> "$STATE"; }

if [ "$REMOVE" = 0 ]; then
  IN_GIT=0
  git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 && IN_GIT=1

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

  # ---- build the new config first: a jq failure leaves nothing behind ----
  CREATED_CONFIG=0; CREATED_MCP=0
  if [ -f "$CONFIG" ]; then INPUT="$(cat "$CONFIG")"; else CREATED_CONFIG=1; INPUT='{}'; fi
  jq -e '.mcp == null' <<<"$INPUT" >/dev/null && CREATED_MCP=1
  new_tmp
  printf '%s' "$INPUT" | jq --arg w "$WRAPPER" '.mcp = ((.mcp // {}) + {"chrome-devtools": {type: "local", command: [$w], enabled: true, timeout: 60000}})' > "$TMP"

  # ---- commit: exclude + state first, then config and symlink ----
  if [ "$IN_GIT" = 1 ]; then
    PREFIX="$(git -C "$DIR" rev-parse --show-prefix)"            # "" at the worktree root, "sub/" below it
    EXCLUDE="$(git -C "$DIR" rev-parse --git-path info/exclude)" # resolves correctly for worktrees
    case "$EXCLUDE" in /*) ;; *) EXCLUDE="$DIR/$EXCLUDE" ;; esac
    P="$(printf '%s' "$PREFIX" | sed -e 's/[][\*?]/\\&/g')"      # escape gitignore specials: [ ] \ * ?
    mkdir -p "$(dirname "$EXCLUDE")"; touch "$EXCLUDE"
    [ -z "$(tail -c1 "$EXCLUDE")" ] || echo >> "$EXCLUDE"
    for l in "/${P}opencode.json" "/${P}.opencode/skills/chrome-devtools" "/${P}.opencode/.browserbase-state"; do
      grep -qxF "$l" "$EXCLUDE" || echo "$l" >> "$EXCLUDE"
    done
  else
    echo "[note] $DIR is not a git repo; make sure opencode.json and .opencode/ never get committed elsewhere." >&2
  fi
  mkdir -p "$SKILLS"
  [ "$CREATED_CONFIG" = 0 ] || state_add created-config
  [ "$CREATED_MCP" = 0 ] || state_add created-mcp
  mv "$TMP" "$CONFIG"; TMP=""
  ln -sfn "$SKILL_SRC" "$LINK"
  exec "${OPENCODE_BIN:-opencode}" "$DIR"
fi

# --- remove: mcp key, symlink, state file. info/exclude is deliberately left alone. -------------
write_json() { new_tmp; jq "$@" "$CONFIG" > "$TMP" && mv "$TMP" "$CONFIG"; TMP=""; }
if [ -f "$CONFIG" ]; then
  if jq -e --arg w "$WRAPPER" '.mcp["chrome-devtools"].command == [$w]' "$CONFIG" >/dev/null 2>&1; then
    write_json 'del(.mcp["chrome-devtools"])'
    echo "[mcp] removed chrome-devtools from $CONFIG"
  fi
  # an empty mcp object we (or an older install) left behind
  if jq -e '. == {"mcp": {}}' "$CONFIG" >/dev/null 2>&1 || { state_has created-mcp && jq -e '.mcp == {}' "$CONFIG" >/dev/null 2>&1; }; then
    write_json 'del(.mcp)'
  fi
  if state_has created-config && jq -e '. == {}' "$CONFIG" >/dev/null 2>&1; then rm "$CONFIG"; fi
fi
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$SKILL_SRC" ]; then rm "$LINK"; fi
rm -f "$STATE"
rmdir "$SKILLS" "$DIR/.opencode" 2>/dev/null || true
echo "[done] removed BrowserBase additions from $DIR (git info/exclude lines, if any, are kept)"

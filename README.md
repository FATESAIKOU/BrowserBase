# BrowserBase

Give AI coding agents a **real, logged-in Chrome** — via the official
[`chrome-devtools-mcp`](https://github.com/ChromeDevTools/chrome-devtools-mcp) server,
with zero custom MCP code and zero hand-written skill to maintain.

## The problem

`chrome-devtools-mcp` can launch its own Chrome (`--userDataDir`, `--headless=false`), but it
launches it through Puppeteer. Google detects a Puppeteer-launched browser as insecure and drops
any Google session in the profile to the logged-out state — so workflows that need a real Google
login die at the sign-in wall.

This is not just the `navigator.webdriver` flag. Removing `--enable-automation` and setting
`--disable-blink-features=AutomationControlled` *does* get `navigator.webdriver === false`, but
Google **still** redirects to the signed-out page. The Puppeteer launch path itself is the tell.

Rolling your own CDP MCP server (the previous approach here) sidesteps that but means maintaining a
browser-automation server by hand, forever, without official support.

## The fix

A ~40-line wrapper. It launches **stock Chrome** the normal way — same binary you click in the
Dock, no automation flags — with the persistent profile and a CDP port, then hands that running
browser to the official MCP server over `--browserUrl`. Attaching to a normally-launched Chrome
does not trip Google's detection.

```
scripts/chrome-devtools-wrapper.sh
  │
  ├─ CDP already up on :9222 ?  → reuse it
  └─ no                         → launch "Google Chrome" with
                                    --user-data-dir=<repo>/user_profile
                                    --remote-debugging-port=9222   (never --headless)
  │
  └─ exec .mcp/…/chrome-devtools-mcp --browserUrl http://127.0.0.1:9222
```

The server and its skill are **vendored** into `.mcp/` (a plain `npm install
chrome-devtools-mcp@latest`). Nothing is hand-authored: the 29 tools and the agent-facing skill
both come straight from Google's package, and re-running an installer updates both to the latest
release. Result: `navigator.webdriver === false`, logins persist in `user_profile/` across
restarts, and the window is always visible.

## Install

```bash
bash scripts/install-claude.sh      # vendors the server; removes old global Claude Code entries
bash scripts/install-opencode.sh    # vendors the server; removes old global OpenCode entries
```

Both are idempotent (re-run to update the vendored server). Neither registers anything globally:
the server's 29 tool definitions and the skill description would otherwise sit in the context of
every session, including the many that never open a browser. Browser sessions opt in with the
launchers below. The installers also clean up what older versions wrote globally. They touch
only the items listed here; everything else in your configs is left as it was:

- Claude Code: the user-scope `chrome-devtools` MCP entry if it points at this repo's wrapper, and
  `~/.claude/skills/chrome-devtools` if it is a symlink to this repo's vendored skill.
- OpenCode: `mcp["chrome-devtools"]` in the global `opencode.json` if it points at the wrapper, and
  `skills/chrome-devtools` if it is a symlink to the vendored skill. Also removed, because older
  versions of this installer wrote them under names of their own, so these names are deleted:
  `mcp["cdp-enh"]`, `permission["cdp_*"]` and `permission.bash` rules ending in
  `start_browser.sh`. If the config is not plain JSON (e.g. JSONC), the installer warns, leaves it
  unchanged and exits non-zero, after having removed the skill link. `update`, all other permissions, other MCP servers and
  `$schema` are untouched. **Not removed:** the `permission.external_directory[<this repo>]` entry
  older versions added; it cannot be told apart from a user's own rule and may be what lets an
  agent read the linked skill, so it stays.

## Start a browser session (Claude Code)

```bash
scripts/browser-claude.sh            # a `claude` session (interactive) plus the chrome-devtools MCP
scripts/browser-claude.sh -p "…"     # session options/prompt are passed through to claude
```

It runs `exec claude "$@" --mcp-config '<json>'` (before a literal `--` if you pass one), where
the JSON points at
`scripts/chrome-devtools-wrapper.sh` (path derived from the script's own location, so nothing is
hard-coded). Chrome is still started by the wrapper, never by Puppeteer.

The flag goes last because `--mcp-config` takes a space-separated list (`<configs...>`): placed
before a bare prompt it would swallow the prompt as another config. This is a session launcher: subcommands
(`doctor`, `mcp`, `update`, …) must not go through this script; run `claude` directly for them. (Not verified against the real parser, only against a stub
that records the arguments.)

`--mcp-config` alone **adds** the server to the session's existing MCP servers. We deliberately do
not pass `--strict-mcp-config`, which would ignore every other MCP server (global, project
`.mcp.json`, claude.ai connectors) and leave only this one.

**Skill: not delivered per session.** Claude Code has `--plugin-dir` (a folder with
`.claude-plugin/plugin.json` and `skills/<name>/SKILL.md` loaded for one session, skills namespaced
as `plugin:skill`), which could carry the skill. It is not wired in because it has not been
verified end to end here (in particular whether a symlinked vendored skill is followed), and
`--add-dir` only widens file access. Without it, the MCP tool descriptions are usually enough; for
extra guidance use `scripts/browser-claude.sh --append-system-prompt "…"` or load the vendored
`SKILL.md` into the prompt yourself.

## Start a browser session (OpenCode)

```bash
scripts/browser-opencode.sh [dir]            # dir defaults to the current directory
scripts/browser-opencode.sh --remove [dir]   # undo
```

OpenCode's MCP servers are started by a shared background service. A project-level `opencode.json`
makes it start the server only for sessions in that directory and stop it afterwards. The script:

1. Merges **only** `mcp["chrome-devtools"]` (local, command = this repo's wrapper, timeout 60000)
   into `<dir>/opencode.json` with `jq`; every other key is kept. A *different* existing
   `chrome-devtools` entry is never overwritten.
2. Symlinks the vendored skill to `<dir>/.opencode/skills/chrome-devtools` (OpenCode's project
   skill path; the docs do not say whether symlinks are followed).
3. Runs `opencode <dir>` (override the command with `OPENCODE_BIN`).

**Keeping it out of git.** In a git repo or worktree the script appends these lines to the repo's
`info/exclude` (resolved with `git rev-parse --git-path`, so worktrees work), skipping lines
already present: `/<dir>/opencode.json`, `/<dir>/.opencode/skills/chrome-devtools` and its own state
file `.opencode/.browserbase-state`, anchored from the worktree root with `[ ] \ * ?` escaped.
If `opencode.json` is already **tracked**, it stops with an error instead of editing it. Outside a
git repo it only prints a reminder.

`--remove` removes the mcp key (only if it is ours), the skill link (only if it is our symlink) and
the state file. It deletes `opencode.json` only if the script created it and it is empty again. It
drops an empty `"mcp": {}` only when the state file says the script introduced the `mcp` key; if
there is no state (e.g. the file was never touched by the script) your own `{"mcp": {}}` is left
alone. A symlinked `opencode.json` (e.g. into a dotfiles repo) stays a symlink: the real file it
points to is updated, keeping its mode.

**It never edits `info/exclude`, so the lines stay in the repo's `.git/info/exclude`** (shared by all
its worktrees). That is not free: from then on `git status` silently hides any untracked
`opencode.json` or `.opencode/skills/chrome-devtools` of your own at the same path in that repo,
including in other worktrees, and `git add opencode.json` is refused ("ignored by .gitignore")
until you remove the lines. To drop exactly the three lines the script adds at the worktree root
(exact whole-line matches, nothing else is touched; for a subdirectory use the same lines prefixed
with `/<subdir>`, with `[ ] \ * ?` backslash-escaped):

```bash
f="$(git rev-parse --git-path info/exclude)"
for l in /opencode.json /.opencode/skills/chrome-devtools /.opencode/.browserbase-state; do
  grep -vxF -- "$l" "$f" > "$f.new" || true; mv "$f.new" "$f"
done
```

Tests: `node --test test/browser-opencode.test.mjs`. They run in throw-away temp dirs with a stub
`claude` and `npm` first on `PATH` and `OPENCODE_BIN=true`; no opencode, claude or Chrome is
started. The install scripts the tests run do create `user_profile/` and chmod the wrapper inside
this repo (`update_vendor`). Covered: config merge/removal and restoring file mode; symlinked configs (absolute and relative links) for both scripts; the cleanup snippet above leaving a user's `/sub/opencode.json`; foreign
entries and links left alone; plain dir, git repo, worktree and worktree subdirectory (`git status`
clean, exclude lines kept by `--remove`, README cleanup command); other worktrees staying clean
after `--remove`; the same-path side effect above; special characters in paths; tracked-file
refusal; a user's own `opencode.json.tmp` never deleted; `install-opencode.sh` (leftover cleanup,
string/JSONC configs, foreign items) and `install-claude.sh` against synthetic configs; and
`browser-claude.sh` argument order.
Not verified: that a real OpenCode picks up the project MCP/skill (including through the symlink),
that a shared OpenCode service stops the server afterwards, and the real `claude` argument parser.

## Use

In a browser session, just ask the agent to browse. The first tool call launches Chrome if it isn't already up.

```
> take a screenshot of my Gmail inbox
> check whether I'm logged into github.com
```

First run only: log into your accounts by hand in the window, once. The session lives in
`user_profile/` and is reused from then on.

## Config

The wrapper reads these env vars (all optional):

| Var | Default |
|---|---|
| `BROWSERBASE_PROFILE` | `<repo>/user_profile` |
| `BROWSERBASE_CDP_PORT` | `9222` |
| `BROWSERBASE_CHROME` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| `BROWSERBASE_MCP_BIN` | `<repo>/.mcp/node_modules/.bin/chrome-devtools-mcp` |

## Notes

- **One Chrome per profile.** `user_profile/` can only be open by a single Chrome process (profile
  lock). Don't point a second browser at it — and never launch it via Puppeteer/`--userDataDir`,
  which invalidates the Google session (see "The problem").
- **Browser outlives the MCP server.** Restarting the agent reuses the open window; closing the
  window means the next tool call opens a fresh one.
- **Never headless**, by design — `--headless` is never passed.
- Verify CDP by hand: `curl -s http://127.0.0.1:9222/json/version`

## Layout

```
scripts/chrome-devtools-wrapper.sh   launches stock Chrome + attaches the official MCP server
scripts/browser-claude.sh            starts Claude Code with the browser MCP attached (per session)
scripts/browser-opencode.sh          adds the browser MCP + skill to one project dir for OpenCode
scripts/install-claude.sh            vendors the server, removes old global Claude Code entries
scripts/install-opencode.sh          vendors the server, removes old global OpenCode entries
scripts/_vendor.sh                   shared: npm install .mcp/ + skill symlink helpers
.mcp/                                vendored chrome-devtools-mcp (server + skills, gitignored)
user_profile/                        persistent Chrome profile (gitignored)
```

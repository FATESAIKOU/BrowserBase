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
bash scripts/install-claude.sh      # Claude Code: vendors the server + skill, removes any old global MCP entry
bash scripts/install-opencode.sh    # OpenCode     (~/.config/opencode)
```

`install-claude.sh` (idempotent; re-run to update):
1. `npm install chrome-devtools-mcp@latest` into `.mcp/`.
2. Removes a `chrome-devtools` MCP entry that points at the wrapper (written by older versions);
   other MCP servers are left alone.
3. Symlinks the package's official `chrome-devtools` skill into `~/.claude/skills`.

It does **not** register the MCP server globally. The server's 29 tool definitions would take up
context in every Claude Code session, including the many that never open a browser.

## Start a browser session (Claude Code)

```bash
scripts/browser-claude.sh            # same as `claude`, plus the chrome-devtools MCP
scripts/browser-claude.sh -p "…"     # any claude arguments are passed through
```

It runs `exec claude --mcp-config '<json>' "$@"`, where the JSON points at
`scripts/chrome-devtools-wrapper.sh` (path derived from the script's own location, so nothing is
hard-coded). Chrome is still started by the wrapper, never by Puppeteer.

`--mcp-config` alone **adds** the server to the session's existing MCP servers. We deliberately do
not pass `--strict-mcp-config`, which would ignore every other MCP server (global, project
`.mcp.json`, claude.ai connectors) and leave only this one.

Skill note: the `chrome-devtools` skill is still linked globally, so its short description is in
every session's context even though it is useless without the MCP tools. Remove
`~/.claude/skills/chrome-devtools` if you want zero footprint outside browser sessions.

Verified: `browser-claude.sh -p` showed the server connected (source `dynamic`) with 29
`mcp__chrome-devtools__*` tools while the other server (claude.ai connector) stayed available; on a
config without the old global entry, `claude mcp list` shows no `chrome-devtools`. Not verified:
interactive (non-`-p`) sessions, and a project-level `.mcp.json` alternative.

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
scripts/install-claude.sh            vendors server + skill, removes old global MCP entry
scripts/install-opencode.sh          vendors + registers for OpenCode
scripts/_vendor.sh                   shared: npm install .mcp/ + skill symlink helpers
.mcp/                                vendored chrome-devtools-mcp (server + skills, gitignored)
user_profile/                        persistent Chrome profile (gitignored)
```

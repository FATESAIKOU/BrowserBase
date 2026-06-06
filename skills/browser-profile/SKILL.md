---
name: browser-profile
description: Use your personal browser profile for authenticated web browsing. Use when asked to browse the web with your own login sessions, check if logged into a site, navigate authenticated pages, fill forms on sites where you are already signed in, extract data from authenticated dashboards, or perform any web automation that needs your personal credentials/cookies. This skill activates a Chrome/Chromium browser with CDP on port 9222 using a saved persistent profile.
---

# Browser Profile — Authenticated Web Automation

A persistent browser (Chrome, Chromium, Brave, or Edge) is launched from the
BrowserBase project root with `start_browser.sh`. All logins, cookies, and
sessions survive restarts because the profile lives in `user_profile/`.

The MCP server `cdp-enh` connects to `localhost:9222` and exposes 20 browser
automation tools.

## Start the Browser

```bash
bash start_browser.sh
```

Extra flags can be passed via environment variables:

| Variable      | Default              | Purpose                                    |
|---------------|----------------------|--------------------------------------------|
| `CHROME`      | (auto-detect)        | Path to the browser executable             |
| `CHROME_ARGS` | (empty)              | Extra Chrome flags (space-separated)       |
| `HEADLESS`    | (empty)              | Set to `1` to run without a GUI window     |
| `CDP_PORT`    | `9222`               | Remote-debugging port                      |
| `PROFILE_DIR` | `./user_profile`     | User-data-dir (persistent cookies/logins)  |

Examples:

```bash
# Normal (GUI) launch with default profile
bash start_browser.sh

# Headless (server / CI)
HEADLESS=1 bash start_browser.sh

# Custom window size + locale
CHROME_ARGS='--window-size=1920,1080 --lang=en' bash start_browser.sh

# Use Brave instead of Chrome
CHROME='/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' bash start_browser.sh

# Alternative port (if 9222 is busy)
CDP_PORT=9333 bash start_browser.sh
```

## Check if the browser is alive

```bash
curl -s http://localhost:9222/json/version | head -5
```

If the browser is not running, the CDP tools will fail.  Start it, then retry.

## Available CDP Tools (20 tools)

### Navigation & Info
- **cdp_navigate(url, wait_ms?)** — Go to a URL. Returns title and URL after load.
- **cdp_page_info()** — Get current URL, title, and readyState in one call.

### Reading Page Content
- **cdp_get_text(selector?, max_length?)** — Extract visible text from the page. Use selector to target specific elements.
- **cdp_get_links(max_results?, filter?)** — List clickable links with href and text. Use filter to narrow by keyword.
- **cdp_get_form_elements(form_selector?)** — List all form inputs/selects/buttons with attributes.

### Interaction
- **cdp_click(text?, selector?, index?)** — Click an element by text content (fuzzy match) or CSS selector.
- **cdp_fill(target, value, by?)** — Fill an input. target can be name, id, selector, or placeholder. by mode: auto (default), name, id, selector, placeholder.
- **cdp_select(target, value?, label?)** — Select an option in a <select> dropdown.
- **cdp_scroll(to?, pixels?, selector?)** — Scroll page. to: "top" or "bottom". Or by pixels. Or to a specific selector.

### Session & Profile (profile-aware tools)
- **cdp_cookies_get(domain?)** — Get cookies for a domain. Use to check if authenticated. Returns cookie names, domains, expiry.
- **cdp_login_check(url, logged_in_selector?, logged_out_selector?)** — Navigate to a URL and check login status. Returns final URL, cookies, page snippet. Use before attempting authenticated actions.
- **cdp_storage(location, key, value?)** — Read/write localStorage or sessionStorage. location: "local" or "session". Many SPAs store auth tokens here.

### Waiting & Debugging
- **cdp_ready(timeout_ms?)** — Wait for page to finish loading (readyState == complete). Use instead of blind cdp_wait.
- **cdp_wait(ms?)** — Pause for N milliseconds. Use after interactions for SPA updates.
- **cdp_console()** — Retrieve captured browser console messages. First call enables capture; subsequent calls return logs. Useful for debugging.

### Tab Management
- **cdp_new_tab(url?)** — Open a new tab.
- **cdp_list_tabs()** — List all open tabs.
- **cdp_switch_tab(index?, match?)** — Switch to a tab by index or URL/title match.

### Screenshot (VISION MODELS ONLY)
- **cdp_screenshot(selector?, quality?)** — Takes a screenshot, saves to a file, returns the file path. Use the Read tool to view it. **Only works if your model can read/see images.** Non-vision models should rely on cdp_get_text + cdp_get_links instead.

### Escape Hatch
- **cdp_evaluate(code, return_result?)** — Execute arbitrary JavaScript. Use for: form submission, data extraction, DOM manipulation. Use IIFE `(() => { ... })()` for multi-line.

## Standard Workflow

### 1. Start the browser (if not running)
```bash
bash start_browser.sh
```
Or verify with: `curl -s http://localhost:9222/json/version`

### 2. Check login status before acting
Use `cdp_login_check` to verify authentication before attempting any authenticated action:
- Check if redirected to login page
- Check for known logged-in selectors (e.g. `.avatar`, `.user-menu`)
- Check domain cookies for session tokens

### 3. For authenticated tasks
1. `cdp_login_check` on the target site
2. If logged in → proceed with navigation, form filling, data extraction
3. If NOT logged in → tell the user they need to log in. NEVER attempt to log in with credentials yourself.
4. Use `cdp_cookies_get` to verify session freshness before important actions

### 4. For data extraction
1. `cdp_navigate` to the page
2. `cdp_ready` to wait for load
3. `cdp_get_text` with specific CSS selectors to extract structured data
4. Alternatively, `cdp_evaluate` for complex JSON extraction

### 5. For form interaction
1. `cdp_get_form_elements` to see all inputs
2. `cdp_fill` each field by name/id/placeholder
3. `cdp_select` for dropdowns
4. `cdp_evaluate` to submit if needed: `document.querySelector('form').submit()`

## Vision vs Non-Vision Models

**Vision-capable models:** Use `cdp_screenshot` to verify visual state, button positions,
CAPTCHAs, or layout issues. The screenshot saves to `screenshots/` directory.

**Non-vision models:** Use `cdp_get_text`, `cdp_get_links`, `cdp_get_form_elements`,
and `cdp_evaluate` to understand the page through its DOM/text. Use `cdp_console`
to debug JS errors.

**Screenshot tool behavior:** `cdp_screenshot` saves files to the project's
`screenshots/` directory. It returns the file path. Use the Read tool with that
path to view the image. Only vision models can interpret screenshots — non-vision
models should skip this tool entirely.

## Profile Location & Cross-Platform

Chrome profile lives at `<project_root>/user_profile/`. Cookies, local storage,
extension data, and all login sessions are persisted here. Do NOT modify files
in this directory directly.

The project works on **macOS** and **Linux**. The `start_browser.sh` script
auto-detects Chrome, Chromium, Brave, and Edge via:

1. The `CHROME` env var (if set)
2. Well-known OS-specific install paths
3. `$PATH` lookup (`google-chrome`, `chromium`, `brave-browser`, `microsoft-edge`)

## Port Conflict

If port `$CDP_PORT` (default 9222) is already in use, kill the existing process:

```bash
lsof -ti:$CDP_PORT | xargs kill -9
```

Then restart with `start_browser.sh`.

## Tools Removed (available via cdp_evaluate alternatives)

- `go_back` → `cdp_evaluate("history.back()")`
- `go_forward` → `cdp_evaluate("history.forward()")`
- `reload` → `cdp_evaluate("location.reload()")`
- `get_url` + `get_title` → merged into `cdp_page_info`

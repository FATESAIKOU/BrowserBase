---
name: browser-profile
description: Use your personal browser profile for authenticated web browsing. Use when asked to browse the web with your own login sessions, check if logged into a site, navigate authenticated pages, fill forms on sites where you are already signed in, extract data from authenticated dashboards, or perform any web automation that needs your personal credentials/cookies.
---

# Browser Profile

A persistent Chrome browser with CDP on port 9222. All logins/cookies survive restarts.

## Install

```bash
cd ~/testAI/BrowserBase
bash install.sh ~/.config/opencode
```

## Start the Browser

```bash
bash start_browser.sh <profile_dir> [headless] [cdp_port] [chrome_path]
```

| Arg | Default | Purpose |
|-----|---------|---------|
| `profile_dir` | *(required)* | `--user-data-dir` (persistent cookies/logins) |
| `headless` | `false` | Set to `true` to run without GUI |
| `cdp_port` | `9222` | Remote-debugging port |
| `chrome_path` | macOS default | Path to Chrome binary |

Examples:

```bash
bash start_browser.sh ./user_profile
bash start_browser.sh ./user_profile true
bash start_browser.sh ./user_profile false 9333
bash start_browser.sh ./user_profile false 9222 "/path/to/chrome"
```

## Check if alive

```bash
curl -s http://localhost:9222/json/version
```

If not running, start it. If port is busy:

```bash
lsof -ti:9222 | xargs kill -9
```

## Available Tools (20)

### Navigation & Info
- **cdp_navigate(url, wait_ms?)** — Go to a URL.
- **cdp_page_info()** — URL + title + readyState in one call.

### Reading
- **cdp_get_text(selector?, max_length?)** — Visible text from the page.
- **cdp_get_links(max_results?, filter?)** — Links with href and text.
- **cdp_get_form_elements(form_selector?)** — All form inputs with attributes.

### Interaction
- **cdp_click(text?, selector?, index?)** — Click by text or CSS selector.
- **cdp_fill(target, value, by?)** — Fill input. by: auto/name/id/selector/placeholder.
- **cdp_select(target, value?, label?)** — Select dropdown option.
- **cdp_scroll(to?, pixels?, selector?)** — Scroll page.

### Session & Profile
- **cdp_cookies_get(domain?)** — Get cookies. Check auth status.
- **cdp_login_check(url, logged_in_selector?, logged_out_selector?)** — Navigate + check login. Returns URL, cookies, page snippet.
- **cdp_storage(location, key, value?)** — Read/write localStorage/sessionStorage.

### Waiting & Debugging
- **cdp_ready(timeout_ms?)** — Wait for readyState=complete.
- **cdp_wait(ms?)** — Pause N ms.
- **cdp_console()** — Capture/retrieve console messages.

### Tab Management
- **cdp_new_tab(url?)** — Open new tab.
- **cdp_list_tabs()** — List tabs.
- **cdp_switch_tab(index?, match?)** — Switch tab.

### Screenshot (vision models only)
- **cdp_screenshot(selector?, quality?)** — Save to file, returns path. Use Read tool to view. Non-vision models: use text/links instead.

### Escape Hatch
- **cdp_evaluate(code)** — Execute JS. Use IIFE `(()=>{...})()`.

## Standard Workflow

### 1. Start + verify
```bash
bash start_browser.sh ./user_profile
curl -s http://localhost:9222/json/version
```

### 2. Check login status
Use `cdp_login_check` before any authenticated action. Check redirect, known selectors, cookies.

### 3. Authenticated tasks
1. `cdp_login_check` → if logged in, proceed
2. If NOT logged in → tell the user. NEVER enter credentials.
3. Use `cdp_cookies_get` to verify session freshness.

### 4. Data extraction
1. `cdp_navigate` → `cdp_ready` → `cdp_get_text` (or `cdp_evaluate` for JSON)

### 5. Forms
1. `cdp_get_form_elements` → `cdp_fill` → `cdp_select` → `cdp_evaluate` to submit

## Screenshot Notes

- Saves to project `screenshots/` directory
- Returns file path — use Read tool to view
- Vision models: use for layout/CAPTCHA checks
- Non-vision models: skip, use `cdp_get_text` + `cdp_get_links` instead

## Profile Location

`<project_root>/user_profile/` — cookies, storage, logins persist here. Do not modify directly.

## Removed Tools (use cdp_evaluate instead)

- `go_back` → `cdp_evaluate("history.back()")`
- `go_forward` → `cdp_evaluate("history.forward()")`
- `reload` → `cdp_evaluate("location.reload()")`
- `get_url` + `get_title` → merged into `cdp_page_info`

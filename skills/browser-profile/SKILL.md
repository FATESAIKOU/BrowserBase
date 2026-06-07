---
name: browser-profile
description: Use your personal browser profile for authenticated web browsing. Use when asked to browse the web with your own login sessions, check if logged into a site, navigate authenticated pages, fill forms on sites where you are already signed in, extract data from authenticated dashboards, or perform any web automation that needs your personal credentials/cookies.
---

# Browser Profile

A persistent Chrome browser with CDP on port 9222. All logins/cookies survive restarts.
Backed by a Python MCP server at `mcp-servers/cdp-server.py`.

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

## Available Tools (28)

### Navigation & Info
- **cdp_navigate(url, wait_ms?)** — Go to a URL.
- **cdp_page_info()** — URL + title + readyState in one call.

### Reading
- **cdp_get_text(selector?, max_length?)** — Visible text from the page.
- **cdp_get_links(max_results?, filter?)** — Links with href and text.
- **cdp_get_form_elements(form_selector?)** — All form inputs with attributes.

### Interaction — High-Level
- **cdp_click(text?, selector?, index?)** — Click by text or CSS selector.
- **cdp_fill(target, value, by?)** — Fill input. by: auto/name/id/selector/placeholder.
- **cdp_select(target, value?, label?)** — Select dropdown option.
- **cdp_scroll(to?, pixels?, selector?)** — Scroll page.

### Interaction — Vision-Enabled (Coordinate-Based)
For SPAs (Gmail, Notion, Linear) or obfuscated UIs where CSS selectors are unreliable.

- **cdp_click_xy(x, y, button?, click_count?, delay_ms?)** — Click at viewport coordinates (CSS pixels) using real CDP mouse events. **Auto-validates target visibility** before clicking; returns `[ERROR] target at (x,y) is hidden` if the element is zero-size, invisible, or behind another element. Preferred over `cdp_click` for SPAs.
- **cdp_click_element_ref(index, manifest_path?)** — Click an element by its `#N` index from the most recent `cdp_screenshot_annotated` manifest. Resolves bounding rect + center + dispatches real mouse events internally — no manual coordinate math. Manifest is cached after each annotated screenshot; pass `manifest_path` to target a specific one. Falls back to scanning `screenshots/` for the newest manifest if cache is empty. **Preferred over `cdp_click_xy` for SPA clicks** when you already have an annotated screenshot.
- **cdp_hover(x, y)** — Hover the mouse at coordinates. Use for triggering dropdown menus, tooltips, expandable UI.
- **cdp_drag(from_x, from_y, to_x, to_y, steps?, delay_ms?)** — Drag from one viewport position to another. Useful for sliders, sortable lists, and gesture-based UI.
- **cdp_key(key, modifiers?, repeat?)** — Send a keyboard key press (keyDown + char + keyUp). Supports `Enter`, `Tab`, `Escape`, `ArrowDown`, `ArrowUp`, `ArrowLeft`, `ArrowRight`, `Backspace`, `Delete`, single chars (`a`, `A`, `!`). Modifiers: `'Ctrl,Shift'`, `'Cmd'`, `'Alt'`.

### Screenshot (vision models)
- **cdp_screenshot(selector?, quality?)** — Save to file, returns path. Use Read tool to view. **Image is auto-resized to CSS pixel dimensions** (1:1 with `cdp_click_xy` coordinates) regardless of OS DPR. `quality` < 100 → JPEG; `quality` >= 100 → PNG.
- **cdp_screenshot_annotated(max_elements?, min_size?, quality?)** — Screenshot with every interactive element outlined + numbered. Writes `annotated_*.png` (resized to CSS 1:1) + `annotated_*.json` manifest. Manifest now includes `image_size: {w, h}` so consumers can verify the coord system. Mapping per `#N`: `{i, tag, text, role, sel, x, y, w, h}`. Detects `<div role="button" data-tooltip="次">`, `[data-testid]`, `[onclick]`, and other SPA patterns. **Next step: `cdp_click_element_ref(N)`** to click `#N` directly (no coordinate math).

### Waiting & Smart Wait
- **cdp_ready(timeout_ms?)** — Wait for readyState=complete.
- **cdp_wait(ms?)** — Blind pause in ms.
- **cdp_wait_for(selector?, text?, state?, timeout_ms?, poll_ms?, match?)** — Poll until element/text matches desired state. `match` modes: `'contains'` (default, unicode-normalized substring), `'exact'`, `'regex'`. Handles i18n characters (CJK, en-dash, zero-width spaces) automatically via NFKC normalization.
- **cdp_fingerprint(selector?, strip_digits?)** — Hash visible page text. Use to detect whether virtualized content (Gmail, Notion, Linear) actually changed after a scroll/page transition. Compare before/after fingerprints.

### Session & Profile
- **cdp_cookies_get(domain?)** — Get cookies. Check auth status.
- **cdp_login_check(url, logged_in_selector?, logged_out_selector?)** — Navigate + check login. Returns URL, cookies, page snippet.
- **cdp_storage(location, key, value?)** — Read/write localStorage/sessionStorage.

### Console & Debug
- **cdp_console()** — Capture/retrieve console messages.

### Tab Management
- **cdp_new_tab(url?)** — Open new tab.
- **cdp_list_tabs()** — List tabs.
- **cdp_switch_tab(index?, match?)** — Switch tab.

### Escape Hatch
- **cdp_evaluate(code, await_promise?, return_result?)** — Execute JS. Set `await_promise=true` for async code. Use IIFE `(()=>{...})()`.

### Batch
- **cdp_batch(steps)** — Execute multiple tool calls sequentially in a single WebSocket connection. Faster than individual calls. Steps: `[{tool, args}, ...]`. Returns one line per step.

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

### 6. Vision-driven interaction (for SPAs / obfuscated UIs)
1. `cdp_screenshot_annotated` → find element `#N` in image
2. `cdp_click_element_ref(N)` — click `#N` directly (preferred)
   - Or read `annotated_*.json` manifest to get `x, y, w, h` and call `cdp_click_xy(x + w/2, y + h/2)` (manual fallback)
3. `cdp_fingerprint` before/after to verify content changed
4. Use `cdp_hover(x, y)` for dropdown menus, `cdp_drag` for sliders
5. Use `cdp_key('Enter')` or `cdp_key('Tab')` for keyboard navigation

### 7. Multi-step operations
1. Build a `cdp_batch` array of steps, e.g.:
   ```json
   [
     {"tool": "cdp_click_xy", "args": {"x": 682, "y": 88}},
     {"tool": "cdp_wait_for", "args": {"text": "51–100", "match": "contains"}},
     {"tool": "cdp_get_text", "args": {"max_length": 5000}}
   ]
   ```
2. Batch reuses the same WebSocket, avoiding TLS handshake overhead.

## Screenshot Notes

- Saves to project `screenshots/` directory
- `cdp_screenshot` returns file path + `Image size: WxH (CSS pixels, click coords 1:1)` line — use Read tool to view
- `cdp_screenshot_annotated` returns annotated PNG + JSON manifest. Manifest now includes `image_size: {w, h}` (same as CSS viewport) so `x + w/2, y + h/2` in manifest == pixel coords in image == CDP `Input.dispatchMouseEvent` coords. No DPR math needed.
- Vision models: use annotated screenshot to locate elements by number, then `cdp_click_element_ref(N)`
- Non-vision models: use `cdp_get_text` + `cdp_get_links` + manifest JSON + `cdp_click_element_ref(N)`

## WebSocket Performance

The MCP server maintains a **persistent WebSocket** to the active Chrome tab.
All tools reuse this connection, avoiding the TLS handshake penalty of
opening a new WS on every single tool call. This reduces per-operation
latency by 20-30%. `cdp_switch_tab` invalidates the session, and the next
tool call automatically reconnects to the newly active tab.

## Profile Location

`<project_root>/user_profile/` — cookies, storage, logins persist here. Do not modify directly.

## Removed Tools (use cdp_evaluate instead)

- `go_back` → `cdp_evaluate("history.back()")`
- `go_forward` → `cdp_evaluate("history.forward()")`
- `reload` → `cdp_evaluate("location.reload()")`
- `get_url` + `get_title` → merged into `cdp_page_info`

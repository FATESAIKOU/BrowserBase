#!/usr/bin/env python3
"""
CDP Browser MCP Server — controls local Chrome via CDP on port 9222.

Tools:
  Navigation:  cdp_navigate, cdp_page_info
  Reading:     cdp_get_text, cdp_get_links, cdp_get_form_elements
  Interaction: cdp_click, cdp_click_xy, cdp_hover, cdp_drag, cdp_key,
               cdp_fill, cdp_select, cdp_scroll
  Screenshot:  cdp_screenshot, cdp_screenshot_annotated
  Wait:        cdp_ready, cdp_wait, cdp_wait_for, cdp_fingerprint
  Session:     cdp_cookies_get, cdp_login_check
  Debug:       cdp_console, cdp_storage
  Tabs:        cdp_new_tab, cdp_list_tabs, cdp_switch_tab
  Utility:     cdp_evaluate, cdp_batch
"""

import asyncio
import json
import os
import base64
import hashlib
import time
import traceback
import unicodedata
import glob

import httpx
import websockets
from websockets.asyncio.client import connect as ws_connect
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ────────────────────────────────────────────────────────────────────

CDP_HOST = os.environ.get("CDP_HOST", "localhost")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_BASE = f"http://{CDP_HOST}:{CDP_PORT}"
WS_MAX_SIZE = 50 * 1024 * 1024
SCREENSHOT_DIR = os.environ.get("CDP_SCREENSHOT_DIR", "./screenshots")

# ── Persistent WebSocket Session ──────────────────────────────────────────────

class _Session:
    """Manages a persistent WebSocket to the active page target.

    Avoids re-opening a WS + TLS handshake on every command.
    Each tool call acquires a lock so messages are ordered.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._ws = None
        self._target_id = None
        self._msg_counter = 0
        # Cache the most recent annotated manifest for cdp_click_element_ref
        self.last_manifest = None  # {"path": str, "elements": [...], "image_size": {...}}

    def _targets(self) -> list[dict]:
        import httpx as _httpx
        with _httpx.Client() as c:
            return c.get(f"{CDP_BASE}/json/list").json()

    def _first_page(self, targets: list[dict]) -> dict | None:
        for t in targets:
            if t.get("type") == "page":
                return t
        return None

    async def _connect(self, page: dict) -> None:
        ws_url = page["webSocketDebuggerUrl"]
        self._ws = await ws_connect(ws_url, max_size=WS_MAX_SIZE)
        self._target_id = page.get("id", "")
        self._msg_counter = 0

    async def _ensure_ws(self):
        if self._ws is not None:
            # Check if still alive
            try:
                pong = await asyncio.wait_for(self._ws.ping(), timeout=2)
                await asyncio.wait_for(pong, timeout=2)
                return  # alive
            except Exception:
                self._ws = None
                self._target_id = None

        # Reconnect
        targets = self._targets()
        page = self._first_page(targets)
        if page is None:
            # Create blank tab
            ver = httpx.get(f"{CDP_BASE}/json/version").json()
            bws_url = ver.get("webSocketDebuggerUrl", "")
            if bws_url:
                async with ws_connect(bws_url) as bws:
                    await bws.send(json.dumps({
                        "id": 1, "method": "Target.createTarget",
                        "params": {"url": "about:blank"}
                    }))
                    await bws.recv()
                await asyncio.sleep(0.6)
                targets = self._targets()
                page = self._first_page(targets)
            if page is None:
                raise RuntimeError("No page target found and could not create one")
        await self._connect(page)
        # NOTE: Don't lock DPR=1 here. Keeping native OS scale factor
        # preserves text/visual rendering quality. Screenshots are resized
        # back to CSS pixel dimensions in _screenshot / _screenshot_annotated
        # so click coordinates stay 1:1 with the saved image.

    async def send(self, method: str, params: dict | None = None) -> dict:
        async with self._lock:
            await self._ensure_ws()
            self._msg_counter += 1
            msg = json.dumps({
                "id": self._msg_counter,
                "method": method,
                "params": params or {}
            })
            await self._ws.send(msg)
            raw = await self._ws.recv()
            result = json.loads(raw)
        if "error" in result:
            return {"error": result["error"]}
        return result.get("result", {})

_session = _Session()

# ── CDP low-level helpers ─────────────────────────────────────────────────────

async def _evaluate(expression: str, await_promise: bool = False) -> str:
    flags = {"expression": expression, "returnByValue": True}
    if await_promise:
        flags["awaitPromise"] = True
        flags["userGesture"] = True
    result = await _session.send("Runtime.evaluate", flags)
    if "result" in result:
        value = result["result"].get("value", "")
        if isinstance(value, str):
            if not value and "exceptionDetails" in result:
                import sys
                exc = result["exceptionDetails"]
                text = exc.get("text", "") or exc.get("exception", {}).get("description", "")
                print(f"[DEBUG] JS exception: {text[:200]}", file=sys.stderr)
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def _ok(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _err(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"[ERROR] {text}")]


def _norm(text: str) -> str:
    """Unicode normalization for fuzzy text matching (NFKC + strip)."""
    return unicodedata.normalize("NFKC", text).strip()

# ── Navigation & Page Info ────────────────────────────────────────────────────

async def _navigate(url: str, wait_ms: int = 2000) -> str:
    await _session.send("Page.navigate", {"url": url})
    await asyncio.sleep(wait_ms / 1000)
    title = await _evaluate("document.title")
    cur_url = await _evaluate("window.location.href")
    return f"Navigated to: {cur_url}\nTitle: {title}"


async def _page_info() -> str:
    code = """(() => {
        return JSON.stringify({url: window.location.href, title: document.title, readyState: document.readyState});
    })()"""
    return await _evaluate(code)

# ── Reading ────────────────────────────────────────────────────────────────────

async def _get_text(selector: str = "body", max_length: int = 8000) -> str:
    js = f"(s => {{ const el = document.querySelector(s); return el ? el.innerText : '(no element found)'; }})({json.dumps(selector)})"
    text = await _evaluate(js)
    if len(text) > max_length:
        text = text[:max_length] + f"\n... [truncated, {len(text)} chars total]"
    return text


async def _get_links(max_results: int = 50, filter_: str | None = None) -> str:
    filter_js = ""
    if filter_:
        filter_js = f".filter(l => l.text.toLowerCase().includes({json.dumps(filter_.lower())}))"
    code = f"""
    (() => {{
        const links = document.querySelectorAll('a[href]');
        const data = [];
        for (const l of links) {{
            data.push({{href: l.href, text: (l.textContent || '').trim().substring(0, 120)}});
            if (data.length >= {max_results * 3}) break;
        }}
        let filtered = data{filter_js};
        if (filtered.length > {max_results}) filtered = filtered.slice(0, {max_results});
        return JSON.stringify(filtered, null, 2);
    }})()
    """
    return await _evaluate(code)


async def _get_form_elements(form_selector: str | None = None) -> str:
    scope = f"document.querySelector({json.dumps(form_selector)}) || " if form_selector else ""
    code = f"""
    (() => {{
        const root = {scope}document;
        const els = root.querySelectorAll('input, select, textarea, button');
        const data = Array.from(els).map((el, i) => ({{
            i,
            tag: el.tagName,
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            placeholder: el.placeholder || '',
            value: el.value || '',
            label: (el.labels && el.labels[0]) ? el.labels[0].textContent.trim() : ''
        }}));
        return JSON.stringify(data, null, 2);
    }})()
    """
    return await _evaluate(code)

# ── Interaction: click ────────────────────────────────────────────────────────

async def _click(text: str | None = None, selector: str | None = None, index: int = 0) -> str:
    if selector:
        code = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return 'ERROR: selector not found';
            el.scrollIntoView({{block: 'center'}});
            el.click();
            return 'clicked selector: {selector}';
        }})()
        """
    elif text:
        code = f"""
        (() => {{
            const text = {json.dumps(text)};
            const candidates = [];
            const walk = (el) => {{
                if (el.nodeType === 3 && el.textContent.includes(text)) candidates.push(el.parentElement);
                if (el.nodeType === 1 && el.textContent.includes(text) && el.children.length === 0) candidates.push(el);
                if (el.nodeType === 1) for (const child of el.children) walk(child);
            }};
            walk(document.body);
            const seen = new Set();
            const unique = [];
            for (const c of candidates) {{
                if (!seen.has(c)) {{ seen.add(c); unique.push(c); }}
            }}
            if (unique.length === 0) return 'ERROR: no element found with text: ' + text;
            const idx = Math.min({index}, unique.length - 1);
            const el = unique[idx];
            el.scrollIntoView({{block: 'center'}});
            el.click();
            return 'clicked text: ' + text + ' (index ' + idx + ' of ' + unique.length + ')';
        }})()
        """
    else:
        return "[ERROR] Must provide either text or selector"
    return await _evaluate(code)

# ── Interaction: coordinate-based click / hover / drag ────────────────────────

async def _click_xy(
    x: float, y: float,
    button: str = "left", click_count: int = 1, delay_ms: int = 80,
) -> str:
    # Visibility check first (#2): ensure the element at (x,y) is visible
    check_js = f"""
    (() => {{
        const el = document.elementFromPoint({x}, {y});
        if (!el) return 'hidden:no-element';
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return 'hidden:zero-size';
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden') return 'hidden:visibility';
        if (cs.display === 'none') return 'hidden:display';
        if (parseFloat(cs.opacity || '1') <= 0) return 'hidden:opacity';
        return 'visible:' + (el.tagName || '?');
    }})()
    """
    vis = await _evaluate(check_js)
    if vis.startswith("hidden"):
        return f"[ERROR] target at ({x}, {y}) is {vis}; use cdp_screenshot_annotated to find visible elements"

    try:
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y, "button": "none"
        })
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": button, "clickCount": click_count
        })
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": button, "clickCount": click_count
        })
    except Exception as e:
        return f"[ERROR] click_xy failed: {e}"
    return f"clicked at ({x}, {y}) button={button} count={click_count}"


async def _click_element_ref(
    index: int,
    manifest_path: str | None = None,
    button: str = "left",
    click_count: int = 1,
    delay_ms: int = 80,
) -> str:
    """#1: Click an element by its index from the most recent cdp_screenshot_annotated manifest.
    Resolves the element's bounding rect and dispatches a real mouse event at the center.
    Avoids manual coordinate math — just say cdp_click_element_ref(5) to click #5.
    """
    # Pick manifest source: explicit path > last_manifest > scan screenshots dir for newest
    manifest = None
    if manifest_path:
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            return f"[ERROR] Failed to read manifest at {manifest_path}: {e}"
    elif _session.last_manifest and os.path.exists(_session.last_manifest["path"]):
        manifest = _session.last_manifest
    else:
        # Fallback: find the newest annotated manifest in screenshots/
        try:
            files = sorted(glob.glob(os.path.join(SCREENSHOT_DIR, "annotated_*.json")),
                           key=os.path.getmtime, reverse=True)
            if files:
                with open(files[0], "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                # Also cache it for future calls
                _session.last_manifest = {
                    "path": files[0],
                    "elements": manifest.get("elements", []),
                    "image_size": manifest.get("image_size", {}),
                }
        except Exception:
            pass

    if not manifest:
        return "[ERROR] No manifest found. Run cdp_screenshot_annotated first to generate one."

    elements = manifest.get("elements", [])
    el = next((e for e in elements if e.get("i") == index), None)
    if not el:
        max_i = max((e.get("i", -1) for e in elements), default=-1)
        return f"[ERROR] Element #{index} not in manifest (valid range: 0..{max_i})"

    x = el["x"] + el["w"] / 2
    y = el["y"] + el["h"] / 2
    # Delegate to _click_xy for the actual mouse event + visibility check
    result = await _click_xy(x, y, button=button, click_count=click_count, delay_ms=delay_ms)
    if result.startswith("[ERROR]"):
        return f"{result} [element #{index} = {el.get('tag', '?')} '{el.get('text', '')[:30]}']"
    return (
        f"clicked element #{index} at ({x:.0f}, {y:.0f}) "
        f"[{el.get('tag', '?')} '{el.get('text', '')[:30]}'] via {manifest.get('image_size') and 'cached manifest'}"
    )


async def _hover(x: float, y: float) -> str:
    try:
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y, "button": "none"
        })
    except Exception as e:
        return f"[ERROR] hover failed: {e}"
    return f"hover at ({x}, {y})"


async def _drag(from_x: float, from_y: float, to_x: float, to_y: float,
                steps: int = 10, delay_ms: int = 20) -> str:
    try:
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": from_x, "y": from_y, "button": "none"
        })
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": from_x, "y": from_y,
            "button": "left", "clickCount": 1
        })
        dx = (to_x - from_x) / steps
        dy = (to_y - from_y) / steps
        for i in range(1, steps + 1):
            await asyncio.sleep(delay_ms / 1000)
            await _session.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": from_x + dx * i, "y": from_y + dy * i,
                "button": "left"
            })
        await _session.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": to_x, "y": to_y,
            "button": "left", "clickCount": 1
        })
    except Exception as e:
        return f"[ERROR] drag failed: {e}"
    return f"dragged from ({from_x}, {from_y}) to ({to_x}, {to_y}) in {steps} steps"

# ── Interaction: keyboard ─────────────────────────────────────────────────────

async def _key_press(key: str, modifiers: str | None = None, repeat: int = 1) -> str:
    """Send a key event via CDP Input domain.

    Args:
        key: Key name (e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown', 'a', 'Backspace')
             or a single character like 'A', '1', '!'.
        modifiers: Comma-separated modifiers, e.g. 'Ctrl,Shift'.
        repeat: Number of times to repeat the key press.
    """
    mod_mask = 0
    if modifiers:
        for m in modifiers.split(","):
            m = m.strip()
            if m in ("Ctrl", "Control"): mod_mask |= 2
            elif m == "Alt": mod_mask |= 1
            elif m == "Shift": mod_mask |= 8
            elif m in ("Meta", "Cmd"): mod_mask |= 4

    # Map common keys to CDP key names
    key_map = {
        "Enter": "\\r", "Tab": "\\t", "Escape": "\\u001b",
        "Backspace": "\\b", "Delete": "\\u007f",
        "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown",
        "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight",
    }

    resolved_key = key_map.get(key, key)
    if len(key) == 1:
        resolved_key = key  # single character

    try:
        for _ in range(repeat):
            await _session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": resolved_key,
                "modifiers": mod_mask,
            })
            if key == "Tab" or key.startswith("Arrow"):
                pass  # no text for navigation keys
            else:
                await _session.send("Input.dispatchKeyEvent", {
                    "type": "char",
                    "text": key if len(key) == 1 else "",
                    "key": resolved_key,
                    "modifiers": mod_mask,
                })
            await _session.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": resolved_key,
                "modifiers": mod_mask,
            })
    except Exception as e:
        return f"[ERROR] key_press failed: {e}"
    return f"key pressed: {key}" + (f" x{repeat}" if repeat > 1 else "")

# ── Interaction: fill / select / scroll ───────────────────────────────────────

async def _fill(target: str, value: str, by: str = "auto") -> str:
    escaped_target = json.dumps(target)
    escaped_value = json.dumps(value)

    if by == "auto":
        code = f"""
        (() => {{
            const v = {escaped_value};
            const t = {escaped_target};
            let el = document.querySelector(t);
            if (!el) el = document.querySelector(`[name="${{t}}"]`);
            if (!el) el = document.querySelector(`[id="${{t}}"]`);
            if (!el) el = document.querySelector(`[placeholder*="${{t}}"]`);
            if (!el) {{
                const labels = document.querySelectorAll('label');
                for (const lbl of labels) {{
                    if (lbl.textContent.trim().includes(t)) {{
                        const for_id = lbl.getAttribute('for');
                        if (for_id) {{ el = document.getElementById(for_id); if (el) break; }}
                        const input = lbl.closest('label')?.querySelector('input, textarea');
                        if (input) {{ el = input; break; }}
                    }}
                }}
            }}
            if (!el) return 'ERROR: no input matched: ' + t;
            el.focus();
            el.value = '';
            el.value = v;
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'filled [' + (el.name || el.id || el.tagName) + '] = ' + v;
        }})()
        """
    elif by == "selector":
        code = f"""
        (() => {{
            const el = document.querySelector({escaped_target});
            if (!el) return 'ERROR: selector not found: ' + {escaped_target};
            el.focus();
            el.value = '';
            el.value = {escaped_value};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'filled [selector] = ' + {escaped_value};
        }})()
        """
    elif by == "name":
        code = f"""
        (() => {{
            const el = document.querySelector(`[name="{escaped_target}"]`);
            if (!el) return 'ERROR: no input with name: ' + {escaped_target};
            el.focus();
            el.value = '';
            el.value = {escaped_value};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'filled [name=' + {escaped_target} + '] = ' + {escaped_value};
        }})()
        """
    elif by == "placeholder":
        code = f"""
        (() => {{
            const el = document.querySelector(`[placeholder*="{escaped_target}"]`);
            if (!el) return 'ERROR: no input with placeholder matching: ' + {escaped_target};
            el.focus();
            el.value = '';
            el.value = {escaped_value};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'filled [placeholder~=' + {escaped_target} + '] = ' + {escaped_value};
        }})()
        """
    elif by == "id":
        code = f"""
        (() => {{
            const el = document.getElementById({escaped_target});
            if (!el) return 'ERROR: no element with id: ' + {escaped_target};
            el.focus();
            el.value = '';
            el.value = {escaped_value};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'filled [id=' + {escaped_target} + '] = ' + {escaped_value};
        }})()
        """
    else:
        return "[ERROR] Unknown 'by' mode: " + by
    return await _evaluate(code)


async def _select(target: str, value: str | None = None, label: str | None = None) -> str:
    escaped_target = json.dumps(target)
    # For template literals, use the raw target escaped for JS string
    raw_target = target.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    if value:
        code = f"""
        (() => {{
            const el = document.querySelector({escaped_target}) || document.querySelector(`select[name="{raw_target}"]`);
            if (!el || el.tagName !== 'SELECT') return 'ERROR: select not found: ' + {escaped_target};
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'selected value=' + {json.dumps(value)} + ' on ' + {escaped_target};
        }})()
        """
    elif label:
        code = f"""
        (() => {{
            const el = document.querySelector({escaped_target}) || document.querySelector(`select[name="{raw_target}"]`);
            if (!el || el.tagName !== 'SELECT') return 'ERROR: select not found: ' + {escaped_target};
            for (const opt of el.options) {{
                if (opt.textContent.trim() === {json.dumps(label)}) {{
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return 'selected label=' + {json.dumps(label)} + ' (value=' + opt.value + ') on ' + {escaped_target};
                }}
            }}
            return 'ERROR: option label not found: ' + {json.dumps(label)} + '. Available: ' + Array.from(el.options).map(o => o.textContent.trim()).join(', ');
        }})()
        """
    else:
        return "[ERROR] Must provide either value or label"
    return await _evaluate(code)


async def _scroll(to: str | None = None, pixels: int | None = None, selector: str | None = None) -> str:
    if selector:
        code = f"document.querySelector({json.dumps(selector)})?.scrollIntoView({{block: 'center'}}); 'scrolled to: {selector}'"
    elif to == "bottom":
        code = "window.scrollTo(0, document.body.scrollHeight); 'scrolled to bottom'"
    elif to == "top":
        code = "window.scrollTo(0, 0); 'scrolled to top'"
    elif pixels is not None:
        code = f"window.scrollBy(0, {pixels}); 'scrolled by {pixels}px'"
    else:
        code = "window.scrollBy(0, 500); 'scrolled down 500px (default)'"
    return await _evaluate(code)

# ── Screenshot ────────────────────────────────────────────────────────────────

async def _screenshot(selector: str | None = None, quality: int = 80) -> str:
    try:
        from PIL import Image
    except ImportError:
        return "[ERROR] PIL (Pillow) is required for auto-CSS resize. Install with: pip install Pillow"
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    quality = min(max(quality, 1), 100)
    fmt_param = "png" if quality >= 100 else "jpeg"
    params = {"format": fmt_param, "quality": quality}
    if selector:
        rect = await _evaluate(
            f"const e=document.querySelector({json.dumps(selector)});"
            f"e ? JSON.stringify(e.getBoundingClientRect()) : 'null'"
        )
        if rect == "null":
            return f"[ERROR] Selector not found: {selector}"
        r = json.loads(rect)
        params["clip"] = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}
    else:
        params["captureBeyondViewport"] = True

    result = await _session.send("Page.captureScreenshot", params)
    if "error" in result:
        return f"[ERROR] Screenshot failed: {result['error']}"
    if "data" not in result:
        return f"[ERROR] Screenshot returned no data"

    raw_data = result["data"]
    try:
        img_bytes = base64.b64decode(raw_data)
    except Exception as e:
        return f"[ERROR] Failed to decode screenshot base64: {e}"

    timestamp = int(time.time() * 1000)
    ext = fmt_param
    filename = f"screenshot_{timestamp}.{ext}"
    filepath = os.path.join(SCREENSHOT_DIR, filename)

    # #3: Resize to CSS pixel dimensions so click coords = image coords 1:1
    # Get viewport size first
    vw_vh = await _evaluate("JSON.stringify({w: innerWidth, h: innerHeight})")
    try:
        vw, vh = json.loads(vw_vh).get("w"), json.loads(vw_vh).get("h")
    except Exception:
        vw, vh = None, None
    # Use clip dims if selector was given
    target_w = (params.get("clip", {}).get("width") if selector else vw)
    target_h = (params.get("clip", {}).get("height") if selector else vh)

    from io import BytesIO
    img = Image.open(BytesIO(img_bytes))
    if target_w and target_h and img.size != (int(target_w), int(target_h)):
        img = img.resize((int(target_w), int(target_h)), Image.LANCZOS)
    save_kwargs = {"quality": quality} if fmt_param == "jpeg" else {"optimize": True}
    img.save(filepath, fmt_param.upper() if fmt_param == "png" else "JPEG", **save_kwargs)

    size_kb = os.path.getsize(filepath) / 1024
    url = await _evaluate("window.location.href")
    return (
        f"Screenshot saved.\n"
        f"File: {filepath}\n"
        f"Size: {size_kb:.1f} KB\n"
        f"Image size: {img.size[0]}x{img.size[1]} (CSS pixels, click coords 1:1)\n"
        f"Format: {fmt_param}\n"
        f"URL: {url}\n"
        f"(Use the Read tool to view the image at the path above)"
    )

# ── Annotated Screenshot ──────────────────────────────────────────────────────

async def _screenshot_annotated(
    max_elements: int = 80, min_size: int = 8, quality: int = 80,
) -> str:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return "[ERROR] PIL (Pillow) is required. Install with: pip install Pillow"

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    quality = min(max(quality, 1), 100)
    fmt_param = "png" if quality >= 100 else "jpeg"

    # Expanded text extraction (#1): include data-tooltip, data-testid, etc.
    elements_js = """
    (() => {
        const SEL = (
            'a, button, input, select, textarea, summary, details, '
            + '[role="button"], [role="link"], [role="checkbox"], [role="radio"], '
            + '[role="menuitem"], [role="tab"], [role="option"], [role="switch"], '
            + '[role="combobox"], [role="listbox"], [role="slider"], '
            + '[onclick], [tabindex]:not([tabindex="-1"])'
        );
        const nodes = document.querySelectorAll(SEL);
        const results = [];
        const vw = window.innerWidth, vh = window.innerHeight;
        let idx = 0;
        for (const el of nodes) {
            const r = el.getBoundingClientRect();
            if (r.width < MIN || r.height < MIN) continue;
            if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
            const cs = getComputedStyle(el);
            if (cs.visibility === 'hidden' || cs.display === 'none') continue;
            if (parseFloat(cs.opacity || '1') <= 0) continue;
            const tag = el.tagName.toLowerCase();
            const type = el.type || '';
            const text = (
                el.innerText
                || el.value
                || el.placeholder
                || el.title
                || el.getAttribute('aria-label')
                || el.getAttribute('data-tooltip')
                || el.getAttribute('data-testid')
                || el.getAttribute('data-cy')
                || el.getAttribute('data-qa')
                || ''
            ).trim().substring(0, 60);
            const role = el.getAttribute('role') || '';
            const sel = (() => {
                if (el.id) return '#' + el.id;
                if (el.name) return tag + '[name="' + el.name + '"]';
                if (el.className && typeof el.className === 'string') {
                    const c = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
                    if (c) return tag + '.' + c;
                }
                return tag;
            })();
            results.push({ i: idx++, tag, type, text, role, sel, x: r.x, y: r.y, w: r.width, h: r.height });
            if (idx >= MAX) break;
        }
        return JSON.stringify({ viewport: { w: vw, h: vh }, elements: results });
    })()
    """.replace("MAX", str(max_elements)).replace("MIN", str(min_size))

    manifest_str = await _evaluate(elements_js)
    try:
        manifest = json.loads(manifest_str)
    except Exception as e:
        return f"[ERROR] Failed to parse element manifest: {e}\nRaw: {manifest_str[:500]}"

    viewport = manifest.get("viewport", {})
    elements = manifest.get("elements", [])

    params = {"format": fmt_param, "quality": quality, "captureBeyondViewport": True}
    result = await _session.send("Page.captureScreenshot", params)
    if "error" in result or "data" not in result:
        return f"[ERROR] Screenshot failed: {result.get('error', 'no data')}"

    img_bytes = base64.b64decode(result["data"])
    timestamp = int(time.time() * 1000)
    raw_path = os.path.join(SCREENSHOT_DIR, f"screenshot_{timestamp}.{fmt_param}")

    # #3: Resize image to CSS pixel dimensions so click coords = image coords 1:1
    from io import BytesIO
    img_raw = Image.open(BytesIO(img_bytes))
    css_w = int(viewport.get("w", img_raw.size[0]))
    css_h = int(viewport.get("h", img_raw.size[1]))
    if img_raw.size != (css_w, css_h):
        img_raw = img_raw.resize((css_w, css_h), Image.LANCZOS)
    img_raw.save(raw_path, fmt_param.upper() if fmt_param == "png" else "JPEG",
                 quality=quality if fmt_param == "jpeg" else None,
                 optimize=True if fmt_param == "png" else False)
    img = img_raw.convert("RGBA")

    # #4: Stamp image_size on manifest so consumers can verify coord system
    manifest["image_size"] = {"w": css_w, "h": css_h}
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font = None
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, 14)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    colors = [
        (255, 0, 0, 220), (0, 128, 255, 220), (0, 200, 0, 220),
        (255, 128, 0, 220), (180, 0, 255, 220), (0, 200, 200, 220),
    ]
    for el in elements:
        c = colors[el["i"] % len(colors)]
        x, y, w, h = el["x"], el["y"], el["w"], el["h"]
        draw.rectangle([x, y, x + w, y + h], outline=c, width=2)
        label = f"#{el['i']}"
        try:
            tb = draw.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except AttributeError:
            tw, th = 20, 16
        lx, ly = x, max(0, y - th - 4)
        draw.rectangle([lx, ly, lx + tw + 6, ly + th + 4], fill=c)
        draw.text((lx + 3, ly + 2), label, fill=(255, 255, 255, 255), font=font)

    img = Image.alpha_composite(img, overlay)
    annotated_path = os.path.join(SCREENSHOT_DIR, f"annotated_{timestamp}.png")
    img.convert("RGB").save(annotated_path, "PNG", optimize=True)
    manifest_path = os.path.join(SCREENSHOT_DIR, f"annotated_{timestamp}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # #1: Cache for cdp_click_element_ref — resolves #N without coordinate math
    _session.last_manifest = {
        "path": manifest_path,
        "elements": elements,
        "image_size": {"w": css_w, "h": css_h},
    }

    size_kb = os.path.getsize(annotated_path) / 1024
    url = await _evaluate("window.location.href")
    return "\n".join([
        f"Annotated screenshot saved.",
        f"Image: {annotated_path} ({size_kb:.1f} KB)",
        f"Manifest: {manifest_path}",
        f"Elements annotated: {len(elements)} / viewport: {viewport.get('w')}x{viewport.get('h')}",
        f"Image size: {css_w}x{css_h} (CSS pixels, click coords 1:1)",
        f"URL: {url}",
        f"",
        f"Manifest format: array of {{i, tag, type, text, role, sel, x, y, w, h, image_size}}",
        f"Use 'sel' to target an element (e.g. document.querySelector('<sel>'))",
        f"Use cdp_click_element_ref(i) to click element #i by manifest index.",
        f"Or use cdp_click_xy(x + w/2, y + h/2) to click by coordinates.",
    ])

# ── Wait tools ─────────────────────────────────────────────────────────────────

async def _ready(timeout_ms: int = 5000) -> str:
    start = time.time()
    while True:
        state = await _evaluate("document.readyState")
        if state == "complete":
            elapsed = int((time.time() - start) * 1000)
            return f"Page ready (state=complete) after {elapsed}ms"
        if (time.time() - start) * 1000 > timeout_ms:
            return f"Page not ready after {timeout_ms}ms (current state={state})"
        await asyncio.sleep(0.3)


async def _wait_for(
    selector: str | None = None,
    text: str | None = None,
    state: str = "visible",
    timeout_ms: int = 5000,
    poll_ms: int = 200,
    match: str = "contains",  # #4: exact | contains | regex
) -> str:
    if not selector and not text:
        return "[ERROR] Must provide selector or text"
    state = state.lower()
    if state not in ("visible", "hidden", "attached", "gone"):
        return f"[ERROR] Unknown state: {state}"

    # Build the match strategy JS (#4) — must be a single expression, no return/const
    match_expr = ""
    norm_text = _norm(text) if text else ""

    if match == "exact" and text:
        match_expr = f"(el.textContent||'').trim().normalize('NFKC').trim()==={json.dumps(norm_text)}"
    elif match == "regex" and text:
        match_expr = f"(function(){{try{{return new RegExp({json.dumps(text)},'i').test(el.textContent||'')}}catch(e){{return false}}}})()"
    else:  # contains (default), with unicode-normalized comparison
        match_expr = f"(el.textContent||'').normalize('NFKC').includes({json.dumps(norm_text)})"

    deadline = time.time() + timeout_ms / 1000
    last_err = ""
    while time.time() < deadline:
        try:
            if selector:
                sel_part = f"const el = document.querySelector({json.dumps(selector)});"
            elif text:
                sel_part = f"""
                let el = null;
                let _found = false;
                const txt = {json.dumps(norm_text)};
                const all = document.body.querySelectorAll('*');
                for (el of all) {{
                    if (el.children.length === 0 && ({match_expr})) {{
                        _found = true; break;
                    }}
                }}
                if (!_found) el = null;
                """

            check_js = f"""
            (() => {{
                {sel_part}
                if (!el) return 'absent';
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                const visible = r.width > 0 && r.height > 0
                    && cs.visibility !== 'hidden' && cs.display !== 'none'
                    && parseFloat(cs.opacity || '1') > 0;
                return JSON.stringify({{found: true, visible, attached: true,
                    rect: {{x: r.x, y: r.y, w: r.width, h: r.height}}}});
            }})()
            """
            result_str = await _evaluate(check_js)
            try:
                info = json.loads(result_str)
            except Exception:
                import sys
                print(f"[DEBUG] FULL check_js:\n{check_js}", file=sys.stderr)
                print(f"[DEBUG] result_str: {result_str[:200]}", file=sys.stderr)
                info = {"found": False, "visible": False}

            is_absent = info.get("found") is None or info.get("found") is False or result_str == "absent"
            is_visible = info.get("visible") is True

            if state == "visible" and is_visible:
                return f"visible (match={match})"
            if state == "attached" and not is_absent:
                return "attached"
            if state in ("hidden", "gone") and is_absent:
                return "absent"
            if state in ("hidden", "gone") and not is_visible:
                return "hidden"
        except Exception as e:
            last_err = str(e)
        await asyncio.sleep(poll_ms / 1000)

    return f"[TIMEOUT] after {timeout_ms}ms waiting for {state} (match={match}, selector={selector}, text={text}). Last: {last_err}"

# ── Fingerprint (#3): detect if virtualized content actually changed ──────────

async def _fingerprint(selector: str = "body", strip_digits: bool = True) -> str:
    """Return a content hash + info to detect if virtualization re-rendered.

    For SPAs that reuse DOM rows (Gmail, Notion, Linear), the same 50 tr elements
    can be re-filled with new data across page transitions. This tool hashes
    visible text content so you can compare before/after fingerprints.

    Args:
        selector: CSS selector of the scroll container (default: body).
        strip_digits: Strip timestamps/dates from hash (default: true) so
                      only structural change affects the hash.
    """
    js = f"""
    (() => {{
        const root = document.querySelector({json.dumps(selector)}) || document.body;
        const text = root.innerText || '';
        const cleaned = {str(strip_digits).lower()}
            ? text.replace(/\\d+/g, '').replace(/\\s+/g, ' ').trim()
            : text.trim();
        const hash = 'sha256:' + Array.from(
            new Uint8Array(
                new Uint8Array(
                    crypto.subtle
                        ? crypto.subtle.digest('SHA-256', new TextEncoder().encode(cleaned))
                        : new Uint8Array(0)
                )
            )
        ).map(b => b.toString(16).padStart(2,'0')).join('').substring(0, 16);
        const lines = text.split('\\n').filter(l => l.trim()).length;
        return JSON.stringify({{hash, lines, chars: text.length, cleaned_chars: cleaned.length}});
    }})()
    """
    return await _evaluate(js)

# ── Console ───────────────────────────────────────────────────────────────────

async def _console() -> str:
    code = """
    (() => {
        if (!window.__cdp_console_buf) {
            window.__cdp_console_buf = [];
            const orig = {};
            ['log','warn','error','info','debug'].forEach(lvl => {
                orig[lvl] = console[lvl];
                console[lvl] = (...args) => {
                    const msg = args.map(a => {
                        try { return typeof a === 'object' ? JSON.stringify(a) : String(a); }
                        catch(e) { return String(a); }
                    }).join(' ');
                    window.__cdp_console_buf.push({lvl, msg: msg.substring(0, 500)});
                    if (window.__cdp_console_buf.length > 200) window.__cdp_console_buf.shift();
                    orig[lvl](...args);
                };
            });
            return 'console capture ENABLED. Call cdp_console again after interacting with the page to read captured messages.';
        }
        const buf = window.__cdp_console_buf.splice(0);
        if (buf.length === 0) return 'no console messages captured yet. Interact with the page, then call cdp_console again.';
        const lines = buf.map(e => '[' + e.lvl.toUpperCase() + '] ' + e.msg);
        return lines.join('\\n');
    })()
    """
    return await _evaluate(code)

# ── Storage ───────────────────────────────────────────────────────────────────

async def _storage(location: str, key: str, value: str | None = None) -> str:
    store = "localStorage" if location in ("local", "localStorage") else "sessionStorage"
    if value is not None:
        code = f"""
        (() => {{
            {store}.setItem({json.dumps(key)}, {json.dumps(value)});
            return 'set {store}.{key} = {value}';
        }})()
        """
    else:
        code = f"""
        (() => {{
            const val = {store}.getItem({json.dumps(key)});
            return val === null ? 'null (not found in {store})' : val;
        }})()
        """
    return await _evaluate(code)

# ── Profile & Session ─────────────────────────────────────────────────────────

async def _cookies_get(domain: str | None = None) -> str:
    params = {}
    if domain:
        urls = [f"{s}{domain}" for s in ("https://", "http://")] + \
               [f"{s}{domain}/*" for s in ("https://", "http://")]
        params["urls"] = urls
    result = await _session.send("Network.getCookies", params)
    cookies = result.get("cookies", [])
    if not cookies:
        return f"No cookies found" + (f" for domain: {domain}" if domain else "")
    lines = [f"{len(cookies)} cookies" + (f" for {domain}:" if domain else ":")]
    for c in cookies:
        flags = ""
        if c.get("secure"): flags += " [secure]"
        if c.get("httpOnly"): flags += " [httpOnly]"
        if c.get("session"): flags += " [session]"
        if c.get("expires") and c["expires"] > 0:
            flags += f" expires={time.strftime('%Y-%m-%d', time.gmtime(c['expires']))}"
        lines.append(
            f"  {c.get('name','?')}={c.get('value','')[:40]}"
            f"  domain={c.get('domain','')}  path={c.get('path','/')}{flags}"
        )
    return "\n".join(lines)


async def _login_check(
    url: str,
    logged_in_selector: str | None = None,
    logged_out_selector: str | None = None,
) -> str:
    await _session.send("Page.navigate", {"url": url})
    await asyncio.sleep(2.5)
    final_url = await _evaluate("window.location.href")
    title = await _evaluate("document.title")
    from urllib.parse import urlparse
    domain = urlparse(url).netloc or urlparse("https://" + url).netloc
    cookies_info = await _cookies_get(domain)
    page_snippet = await _get_text("body", 800)
    checks = []
    if logged_in_selector:
        found = await _evaluate(
            f"document.querySelector({json.dumps(logged_in_selector)}) ? 'found' : 'not found'"
        )
        checks.append(f"  logged_in_selector '{logged_in_selector}': {found}")
    if logged_out_selector:
        found = await _evaluate(
            f"document.querySelector({json.dumps(logged_out_selector)}) ? 'found' : 'not found'"
        )
        checks.append(f"  logged_out_selector '{logged_out_selector}': {found}")
    parts = [
        f"Login check for: {url}",
        f"Final URL:   {final_url}",
        f"Title:       {title}", "",
        cookies_info, "",
        "Page snippet (first 800 chars):", page_snippet,
    ]
    if checks:
        parts.append("\nSelector checks:")
        parts.extend(checks)
    return "\n".join(parts)

# ── Tab Management ────────────────────────────────────────────────────────────

def _tab_targets() -> list[dict]:
    return httpx.get(f"{CDP_BASE}/json/list").json()

async def _new_tab(url: str = "about:blank") -> str:
    ver = httpx.get(f"{CDP_BASE}/json/version").json()
    browser_ws = ver.get("webSocketDebuggerUrl", "")
    if not browser_ws:
        return "[ERROR] Cannot connect to browser"
    async with ws_connect(browser_ws) as ws:
        await ws.send(json.dumps({
            "id": 1, "method": "Target.createTarget",
            "params": {"url": url}
        }))
        resp = await ws.recv()
        result = json.loads(resp)
    target_id = result.get("result", {}).get("targetId", "")
    return f"New tab created: {target_id}\nURL: {url}"


async def _list_tabs() -> str:
    targets = _tab_targets()
    pages = [(i, t) for i, t in enumerate(targets) if t.get("type") == "page"]
    lines = [f"[{i}] {(t.get('title','') or '')[:60]} — {(t.get('url','') or '')[:80]}" for i, t in pages]
    return "\n".join(lines) if lines else "No open tabs"


async def _switch_tab(index: int | None = None, match: str | None = None) -> str:
    targets = _tab_targets()
    pages = [t for t in targets if t.get("type") == "page"]
    target = None
    if index is not None and 0 <= index < len(pages):
        target = pages[index]
    elif match:
        ml = match.lower()
        for p in pages:
            if ml in (p.get("title", "") + p.get("url", "")).lower():
                target = p
                break
    if target is None:
        return f"[ERROR] Tab not found. Available:\n{await _list_tabs()}"
    try:
        ver = httpx.get(f"{CDP_BASE}/json/version").json()
        browser_ws = ver.get("webSocketDebuggerUrl", "")
        if browser_ws:
            async with ws_connect(browser_ws) as ws:
                await ws.send(json.dumps({
                    "id": 1, "method": "Target.activateTarget",
                    "params": {"targetId": target["id"]}
                }))
                await ws.recv()
    except Exception:
        pass
    # Invalidate the global session so next call reconnects to the active tab
    global _session
    _session = _Session()
    return f"Switched to: [{target.get('title', '')}] {target.get('url', '')}"

# ── Batch (#12) ───────────────────────────────────────────────────────────────

async def _batch(steps: list[dict]) -> str:
    """Execute a list of tool calls sequentially in a single session.

    Each step: {"tool": "cdp_click_xy", "args": {"x": 682, "y": 88}}

    Returns one line per step with its result.
    """
    if not isinstance(steps, list):
        return "[ERROR] steps must be a JSON array of {tool, args} objects"
    lines = []
    for i, s in enumerate(steps):
        tool = s.get("tool", "")
        args = s.get("args", {})
        try:
            res = await _dispatch(tool, args)
            text = " ".join(c.text for c in res)
            lines.append(f"[{i+1}] {tool}: {text[:200]}")
        except Exception as e:
            lines.append(f"[{i+1}] {tool}: ERROR {e}")
    return "\n".join(lines)

# ── MCP Server Definition ─────────────────────────────────────────────────────

server = Server("cdp-browser-enhanced")

TOOLS = [
    Tool(name="cdp_navigate",
         description="Navigate the browser to a URL. Returns page title and URL after load.",
         inputSchema={"type":"object","properties":{
             "url":{"type":"string","description":"Full URL to navigate to"},
             "wait_ms":{"type":"integer","description":"Extra wait time after navigation in ms (default 2000)","default":2000}},
             "required":["url"]}),
    Tool(name="cdp_page_info",
         description="Get current page info: URL, title, and readyState in one call.",
         inputSchema={"type":"object","properties":{}}),

    # Reading
    Tool(name="cdp_get_text",
         description="Get visible text content of the current page. Returns trimmed plain text (max first 8000 chars).",
         inputSchema={"type":"object","properties":{
             "selector":{"type":"string","description":"CSS selector to extract text from (default: body)","default":"body"},
             "max_length":{"type":"integer","description":"Max chars to return (default 8000)","default":8000}}}),
    Tool(name="cdp_get_links",
         description="List all links on the page with href and visible text. Returns JSON array.",
         inputSchema={"type":"object","properties":{
             "max_results":{"type":"integer","description":"Max number of links (default 50)","default":50},
             "filter":{"type":"string","description":"Text filter — only links whose text contains this string"}}}),
    Tool(name="cdp_get_form_elements",
         description="List all form elements (input, select, textarea, button) on the page with their attributes.",
         inputSchema={"type":"object","properties":{
             "form_selector":{"type":"string","description":"CSS selector to limit to a specific form (default: all forms)"}}}),

    # Interaction — legacy click
    Tool(name="cdp_click",
         description="Click an element on the page by text content or CSS selector.",
         inputSchema={"type":"object","properties":{
             "text":{"type":"string","description":"Visible text of the element to click (fuzzy match). Only used if selector is not provided."},
             "selector":{"type":"string","description":"CSS selector. Prefer this over text when possible."},
             "index":{"type":"integer","description":"If multiple elements match text, click the Nth (0-based, default 0)","default":0}}}),
    Tool(name="cdp_fill",
         description="Fill an input/textarea field. Supports name, id, CSS selector, or placeholder matching.",
         inputSchema={"type":"object","properties":{
             "target":{"type":"string","description":"Value to match: name, id, CSS selector, or placeholder text"},
             "value":{"type":"string","description":"Text value to fill"},
             "by":{"type":"string","enum":["auto","name","id","selector","placeholder"],"description":"Match mode (default auto tries all)","default":"auto"}},
             "required":["target","value"]}),
    Tool(name="cdp_select",
         description="Select an option in a <select> dropdown by value or display text.",
         inputSchema={"type":"object","properties":{
             "target":{"type":"string","description":"CSS selector, name, or id of the <select> element"},
             "value":{"type":"string","description":"Option value attribute to select"},
             "label":{"type":"string","description":"Option display text to select (used if value not provided)"}},
             "required":["target"]}),
    Tool(name="cdp_scroll",
         description="Scroll the page. Can scroll by pixels, to bottom, or to a specific element.",
         inputSchema={"type":"object","properties":{
             "to":{"type":"string","enum":["bottom","top"],"description":"Scroll to bottom or top of page"},
             "pixels":{"type":"integer","description":"Scroll down by this many pixels"},
             "selector":{"type":"string","description":"Scroll this element into view"}}}),

    # Interaction — coordinate-based (vision-enabled)
    Tool(name="cdp_click_xy",
         description="Click at viewport coordinates (CSS pixels) using real mouse events (Input.dispatchMouseEvent). More reliable than elementFromPoint().click() for SPAs that listen to native mouse events. Automatically checks target visibility before clicking.",
         inputSchema={"type":"object","properties":{
             "x":{"type":"number","description":"X coordinate in CSS pixels"},
             "y":{"type":"number","description":"Y coordinate in CSS pixels"},
             "button":{"type":"string","enum":["left","right","middle"],"description":"Mouse button (default: left)","default":"left"},
             "click_count":{"type":"integer","description":"Click count: 1 for single, 2 for double (default 1)","default":1},
             "delay_ms":{"type":"integer","description":"Delay between mousedown and mouseup in ms (default 80)","default":80}},
             "required":["x","y"]}),
    Tool(name="cdp_click_element_ref",
         description="Click an element by its index from the most recent cdp_screenshot_annotated manifest (#1, #5, etc.). Avoids manual coordinate math. The manifest is cached automatically; optionally pass manifest_path to target a specific one. Falls back to scanning screenshots/ for the newest manifest if cache is empty.",
         inputSchema={"type":"object","properties":{
             "index":{"type":"integer","description":"Element index from manifest (0-based, matches the #N label in the annotated image)"},
             "manifest_path":{"type":"string","description":"Path to a specific annotated manifest JSON. If omitted, uses the most recent manifest (cached or newest on disk)."},
             "button":{"type":"string","enum":["left","right","middle"],"description":"Mouse button (default: left)","default":"left"},
             "click_count":{"type":"integer","description":"Click count: 1 for single, 2 for double (default 1)","default":1}},
             "required":["index"]}),
    Tool(name="cdp_hover",
         description="Hover the mouse at viewport coordinates. Useful for triggering hover menus, tooltips, and expandable UI patterns in SPAs.",
         inputSchema={"type":"object","properties":{
             "x":{"type":"number","description":"X coordinate in CSS pixels"},
             "y":{"type":"number","description":"Y coordinate in CSS pixels"}},
             "required":["x","y"]}),
    Tool(name="cdp_drag",
         description="Drag from one viewport position to another. Useful for sliders, sortable lists, and gesture-based UI.",
         inputSchema={"type":"object","properties":{
             "from_x":{"type":"number"}, "from_y":{"type":"number"},
             "to_x":{"type":"number"}, "to_y":{"type":"number"},
             "steps":{"type":"integer","description":"Number of intermediate points (default 10)","default":10},
             "delay_ms":{"type":"integer","description":"Delay between each step in ms (default 20)","default":20}},
             "required":["from_x","from_y","to_x","to_y"]}),
    Tool(name="cdp_key",
         description="Send a keyboard key press (keyDown + char + keyUp). Supports modifiers like 'Ctrl,Shift' and single characters.",
         inputSchema={"type":"object","properties":{
             "key":{"type":"string","description":"Key name (e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown', 'a', 'A', '!')"},
             "modifiers":{"type":"string","description":"Comma-separated modifiers, e.g. 'Ctrl,Shift' or 'Cmd'"},
             "repeat":{"type":"integer","description":"Repeat count (default 1)","default":1}},
             "required":["key"]}),

    # Screenshot
    Tool(name="cdp_screenshot",
         description="Take a screenshot of the current page and save to a file. Returns the file path — use the Read tool to view the image. Only works if your model has vision/image-reading capability.",
         inputSchema={"type":"object","properties":{
             "selector":{"type":"string","description":"CSS selector to screenshot a specific element (default: full page)"},
             "quality":{"type":"integer","description":"JPEG quality 1-100, or 100+ for PNG (default 80)","default":80}}}),
    Tool(name="cdp_screenshot_annotated",
         description="Take a screenshot with every interactive element (links, buttons, inputs, div[role=button], [data-tooltip], etc.) outlined in colored boxes and labeled with a number. Also writes a JSON manifest mapping each #N to its DOM info (tag, text, role, selector, x/y/w/h). Use this to decide where to click visually, then call cdp_click_xy(x + w/2, y + h/2).",
         inputSchema={"type":"object","properties":{
             "max_elements":{"type":"integer","description":"Cap on number of elements to annotate (default 80)","default":80},
             "min_size":{"type":"integer","description":"Skip boxes smaller than this many pixels (default 8)","default":8},
             "quality":{"type":"integer","description":"JPEG quality 1-100, 100+ for PNG (default 80)","default":80}}}),

    # Wait
    Tool(name="cdp_ready",
         description="Wait for the page to finish loading (document.readyState === 'complete'). Polls until ready or timeout. Use after navigate instead of blind cdp_wait.",
         inputSchema={"type":"object","properties":{
             "timeout_ms":{"type":"integer","description":"Max milliseconds to wait (default 5000)","default":5000}}}),
    Tool(name="cdp_wait",
         description="Wait/pause for a specified duration. Useful after navigation or before reading newly loaded content.",
         inputSchema={"type":"object","properties":{
             "ms":{"type":"integer","description":"Wait time in milliseconds (default 1000)","default":1000}}}),
    Tool(name="cdp_wait_for",
         description="Smart wait: poll until a CSS selector or visible text appears/disappears or matches a desired state. Supports match modes: 'exact', 'contains' (default), 'regex' for flexible text matching across i18n interfaces.",
         inputSchema={"type":"object","properties":{
             "selector":{"type":"string","description":"CSS selector to wait for"},
             "text":{"type":"string","description":"Visible text to wait for (substring match). Use when selector is unstable."},
             "state":{"type":"string","enum":["visible","hidden","attached","gone"],"description":"Target state (default: visible)","default":"visible"},
             "timeout_ms":{"type":"integer","description":"Max wait time in ms (default 5000)","default":5000},
             "poll_ms":{"type":"integer","description":"Polling interval in ms (default 200)","default":200},
             "match":{"type":"string","enum":["contains","exact","regex"],"description":"How to match text: 'contains' (substring, unicode-normalized), 'exact', or 'regex'","default":"contains"}}}),
    Tool(name="cdp_fingerprint",
         description="Return a content hash of visible page text. Use to detect if virtualized content (Gmail, Notion, Linear) actually changed after a page transition. Compare before/after fingerprints to know if data really changed.",
         inputSchema={"type":"object","properties":{
             "selector":{"type":"string","description":"CSS selector of the data container (default: body)","default":"body"},
             "strip_digits":{"type":"boolean","description":"Strip digits from text before hashing (default: true)","default":True}}}),

    # Session & Profile
    Tool(name="cdp_cookies_get",
         description="Get cookies stored in the browser for a given domain. Use to check if the user is logged into a site. Returns cookie names, domains, and expiry info.",
         inputSchema={"type":"object","properties":{
             "domain":{"type":"string","description":"Domain to get cookies for (e.g. 'github.com'). If omitted, returns all cookies."}}}),
    Tool(name="cdp_login_check",
         description="Navigate to a URL and check if the user appears to be logged in. Returns final URL (check for redirect to login page), cookies for the domain, page snippet, and optional selector checks. Use this before attempting authenticated actions.",
         inputSchema={"type":"object","properties":{
             "url":{"type":"string","description":"URL to check login status on"},
             "logged_in_selector":{"type":"string","description":"CSS selector that should exist if logged in"},
             "logged_out_selector":{"type":"string","description":"CSS selector that should exist if logged out"}},
             "required":["url"]}),

    # Console & Storage
    Tool(name="cdp_console",
         description="Capture and retrieve browser console messages. First call enables capture; subsequent calls return captured messages.",
         inputSchema={"type":"object","properties":{}}),
    Tool(name="cdp_storage",
         description="Read or write localStorage/sessionStorage.",
         inputSchema={"type":"object","properties":{
             "location":{"type":"string","enum":["local","session"],"description":"localStorage or sessionStorage"},
             "key":{"type":"string","description":"Storage key to read/write"},
             "value":{"type":"string","description":"Value to set (omit to read/get)"}},
             "required":["location","key"]}),

    # Tab Management
    Tool(name="cdp_new_tab",
         description="Create a new browser tab and optionally navigate to a URL.",
         inputSchema={"type":"object","properties":{
             "url":{"type":"string","description":"URL to navigate the new tab to (default: about:blank)","default":"about:blank"}}}),
    Tool(name="cdp_list_tabs",
         description="List all open browser tabs with URL and title.",
         inputSchema={"type":"object","properties":{}}),
    Tool(name="cdp_switch_tab",
         description="Switch to a tab by index (0-based, from cdp_list_tabs) or URL/title match.",
         inputSchema={"type":"object","properties":{
             "index":{"type":"integer","description":"Tab index from cdp_list_tabs"},
             "match":{"type":"string","description":"Switch to tab whose URL or title contains this string"}}}),

    # Utility
    Tool(name="cdp_evaluate",
         description="Execute arbitrary JavaScript in the page and return the result. Set await_promise=true for async code. Escape hatch for operations not covered by other tools.",
         inputSchema={"type":"object","properties":{
             "code":{"type":"string","description":"JavaScript code to execute. Use IIFE (() => {...}) for multi-line."},
             "await_promise":{"type":"boolean","description":"If true, await the result if code returns a Promise (default false)","default":False},
             "return_result":{"type":"boolean","description":"If false, just execute with no return value (default true)","default":True}},
             "required":["code"]}),
    Tool(name="cdp_batch",
         description="Execute multiple tool calls sequentially in one session. Each step: {tool: 'cdp_click_xy', args: {x: 100, y: 200}}. More efficient than individual calls because it reuses the same WebSocket connection.",
         inputSchema={"type":"object","properties":{
             "steps":{"type":"array","items":{"type":"object","properties":{
                 "tool":{"type":"string"}, "args":{"type":"object"}},
                 "required":["tool","args"]},
                 "description":"Array of {tool, args} objects to execute in order"}},
             "required":["steps"]}),
]

# ── MCP Dispatch ──────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        return await _dispatch(name, arguments)
    except Exception as exc:
        return _err(f"{exc}\n{traceback.format_exc()}")


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    match name:
        case "cdp_navigate":
            return _ok(await _navigate(args["url"], args.get("wait_ms", 2000)))
        case "cdp_page_info":
            return _ok(await _page_info())
        case "cdp_get_text":
            return _ok(await _get_text(args.get("selector", "body"), args.get("max_length", 8000)))
        case "cdp_get_links":
            return _ok(await _get_links(args.get("max_results", 50), args.get("filter")))
        case "cdp_get_form_elements":
            return _ok(await _get_form_elements(args.get("form_selector")))
        case "cdp_click":
            return _ok(await _click(args.get("text"), args.get("selector"), args.get("index", 0)))
        case "cdp_fill":
            return _ok(await _fill(args["target"], args["value"], args.get("by", "auto")))
        case "cdp_select":
            return _ok(await _select(args["target"], args.get("value"), args.get("label")))
        case "cdp_scroll":
            return _ok(await _scroll(args.get("to"), args.get("pixels"), args.get("selector")))
        case "cdp_click_xy":
            return _ok(await _click_xy(args["x"], args["y"], args.get("button", "left"), args.get("click_count", 1), args.get("delay_ms", 80)))
        case "cdp_click_element_ref":
            return _ok(await _click_element_ref(args["index"], args.get("manifest_path"), args.get("button", "left"), args.get("click_count", 1), args.get("delay_ms", 80)))
        case "cdp_hover":
            return _ok(await _hover(args["x"], args["y"]))
        case "cdp_drag":
            return _ok(await _drag(args["from_x"], args["from_y"], args["to_x"], args["to_y"], args.get("steps", 10), args.get("delay_ms", 20)))
        case "cdp_key":
            return _ok(await _key_press(args["key"], args.get("modifiers"), args.get("repeat", 1)))
        case "cdp_screenshot":
            return _ok(await _screenshot(args.get("selector"), args.get("quality", 80)))
        case "cdp_screenshot_annotated":
            return _ok(await _screenshot_annotated(args.get("max_elements", 80), args.get("min_size", 8), args.get("quality", 80)))
        case "cdp_ready":
            return _ok(await _ready(args.get("timeout_ms", 5000)))
        case "cdp_wait":
            await asyncio.sleep(args.get("ms", 1000) / 1000)
            return _ok(f"Waited {args.get('ms', 1000)}ms")
        case "cdp_wait_for":
            return _ok(await _wait_for(args.get("selector"), args.get("text"), args.get("state", "visible"), args.get("timeout_ms", 5000), args.get("poll_ms", 200), args.get("match", "contains")))
        case "cdp_fingerprint":
            return _ok(await _fingerprint(args.get("selector", "body"), args.get("strip_digits", True)))
        case "cdp_cookies_get":
            return _ok(await _cookies_get(args.get("domain")))
        case "cdp_login_check":
            return _ok(await _login_check(args["url"], args.get("logged_in_selector"), args.get("logged_out_selector")))
        case "cdp_console":
            return _ok(await _console())
        case "cdp_storage":
            return _ok(await _storage(args["location"], args["key"], args.get("value")))
        case "cdp_new_tab":
            return _ok(await _new_tab(args.get("url", "about:blank")))
        case "cdp_list_tabs":
            return _ok(await _list_tabs())
        case "cdp_switch_tab":
            return _ok(await _switch_tab(args.get("index"), args.get("match")))
        case "cdp_evaluate":
            result = await _evaluate(args["code"], args.get("await_promise", False))
            return _ok(result)
        case "cdp_batch":
            return _ok(await _batch(args["steps"]))
        case _:
            return _err(f"Unknown tool: {name}")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())

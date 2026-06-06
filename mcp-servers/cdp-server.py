#!/usr/bin/env python3
"""
CDP Browser MCP Server (enhanced) — controls local Chrome via CDP on port 9222.
Enhanced with profile-aware tools: cookies, login check, console, storage, fixed screenshot.
Removed: go_back, go_forward, reload (use cdp_evaluate instead).
Merged: get_url + get_title → cdp_page_info.
"""

import asyncio
import json
import os
import base64
import time
import traceback

import httpx
import websockets
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ────────────────────────────────────────────────────────────────────

CDP_HOST = os.environ.get("CDP_HOST", "localhost")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_BASE = f"http://{CDP_HOST}:{CDP_PORT}"
WS_MAX_SIZE = 50 * 1024 * 1024
SCREENSHOT_DIR = os.environ.get("CDP_SCREENSHOT_DIR", "./screenshots")


# ── CDP low-level helpers ─────────────────────────────────────────────────────


async def _cdp_call(method: str, params: dict | None = None) -> dict:
    """Fetch first page target, connect via WebSocket, send CDP command, return result dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_BASE}/json/list")
        targets: list[dict] = resp.json()

    page = _first_page(targets)
    if page is None:
        page = await _create_blank_tab()
        if page is None:
            raise RuntimeError("No page target found and could not create one")

    ws_url = page["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=WS_MAX_SIZE) as ws:
        msg = json.dumps({"id": 1, "method": method, "params": params or {}})
        await ws.send(msg)
        raw = await ws.recv()
        result = json.loads(raw)

    if "error" in result:
        return {"error": result["error"]}
    return result.get("result", {})


async def _evaluate(expression: str) -> str:
    """Evaluate JS and return the result value as a string."""
    result = await _cdp_call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if "result" in result:
        value = result["result"].get("value", "")
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def _first_page(targets: list[dict]) -> dict | None:
    for t in targets:
        if t.get("type") == "page":
            return t
    return None


async def _create_blank_tab() -> dict | None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{CDP_BASE}/json/version")
        browser_ws = r.json().get("webSocketDebuggerUrl", "")
    if not browser_ws:
        return None
    async with websockets.connect(browser_ws) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": "about:blank"}}))
        await ws.recv()
    await asyncio.sleep(0.6)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_BASE}/json/list")
        return _first_page(resp.json())


def _ok(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _err(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"[ERROR] {text}")]


# ── Navigation & Page Info ────────────────────────────────────────────────────


async def _navigate(url: str, wait_ms: int = 2000) -> str:
    await _cdp_call("Page.navigate", {"url": url})
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


# ── Interaction ───────────────────────────────────────────────────────────────


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
            const el = document.querySelector(`[name="${{{{escaped_target}}}}"]`);
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
            const el = document.querySelector(`[placeholder*="${{{{escaped_target}}}}"]`);
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
    if value:
        code = f"""
        (() => {{
            const el = document.querySelector({escaped_target}) || document.querySelector(`select[name="${{{{escaped_target}}}}"]`);
            if (!el || el.tagName !== 'SELECT') return 'ERROR: select not found: ' + {escaped_target};
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return 'selected value=' + {json.dumps(value)} + ' on ' + {escaped_target};
        }})()
        """
    elif label:
        code = f"""
        (() => {{
            const el = document.querySelector({escaped_target}) || document.querySelector(`select[name="${{{{escaped_target}}}}"]`);
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


# ── Coordinate-based Click (#1) ───────────────────────────────────────────────


async def _click_xy(
    x: float,
    y: float,
    button: str = "left",
    click_count: int = 1,
    delay_ms: int = 50,
) -> str:
    """Click at absolute viewport coordinates (CSS pixels).

    Generates real mouse events via Input.dispatchMouseEvent, including
    move + pressed + released. This is more reliable than elementFromPoint().click()
    for SPAs that listen to native mouse events (e.g. Gmail, Google Docs).

    Args:
        x, y: CSS pixel coordinates inside the viewport.
        button: 'left' | 'right' | 'middle'.
        click_count: 1 for single click, 2 for double click.
        delay_ms: Delay between pressed and released events.
    """
    try:
        await _cdp_call("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y, "button": "none"
        })
        await _cdp_call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": button, "clickCount": click_count
        })
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        await _cdp_call("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": button, "clickCount": click_count
        })
    except Exception as e:
        return f"[ERROR] click_xy failed: {e}"
    return f"clicked at ({x}, {y}) button={button} count={click_count}"


# ── Smart Wait (#2) ──────────────────────────────────────────────────────────


async def _wait_for(
    selector: str | None = None,
    text: str | None = None,
    state: str = "visible",
    timeout_ms: int = 5000,
    poll_ms: int = 200,
) -> str:
    """Wait for an element to appear or reach a desired state.

    Args:
        selector: CSS selector to wait for.
        text: Visible text to wait for (substring match).
        state: 'visible' (default), 'hidden', 'attached' (in DOM), or 'gone'.
        timeout_ms: Max wait time.
        poll_ms: Polling interval.

    Returns: 'found' / 'state reached' or '[TIMEOUT] elapsed=...'
    """
    if not selector and not text:
        return "[ERROR] Must provide selector or text"
    state = state.lower()
    if state not in ("visible", "hidden", "attached", "gone"):
        return f"[ERROR] Unknown state: {state}"

    deadline = time.time() + timeout_ms / 1000
    last_err = ""

    while time.time() < deadline:
        try:
            check_js = """
            (() => {
                let el = null;
                if (SEL) {
                    el = document.querySelector(SEL);
                } else if (TXT) {
                    const t = TXT;
                    const all = document.querySelectorAll('*');
                    for (const n of all) {
                        if (n.children.length === 0 && (n.textContent || '').includes(t)) {
                            el = n; break;
                        }
                    }
                }
                if (!el) return 'absent';
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                const visible = r.width > 0 && r.height > 0
                    && cs.visibility !== 'hidden' && cs.display !== 'none'
                    && parseFloat(cs.opacity || '1') > 0;
                return JSON.stringify({found: true, visible, attached: true,
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height}});
            })()
            """.replace("SEL", json.dumps(selector) if selector else "''") \
               .replace("TXT", json.dumps(text) if text else "''")

            result_str = await _evaluate(check_js)
            try:
                info = json.loads(result_str)
            except Exception:
                info = {"found": False, "visible": False}

            is_absent = info.get("found") is None or (isinstance(info.get("found"), bool) and not info["found"]) or result_str == "absent"
            is_visible = info.get("visible") is True

            if state == "visible" and is_visible:
                rect = info.get("rect", {})
                return f"visible at ({rect.get('x', '?')}, {rect.get('y', '?')}) size={rect.get('w', '?')}x{rect.get('h', '?')}"
            if state == "attached" and not is_absent:
                return "attached"
            if state in ("hidden", "gone") and is_absent:
                return "absent"
            if state in ("hidden", "gone") and not is_visible:
                return "hidden"
        except Exception as e:
            last_err = str(e)

        await asyncio.sleep(poll_ms / 1000)

    return f"[TIMEOUT] after {timeout_ms}ms waiting for {state} (selector={selector}, text={text}). Last: {last_err}"


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


# ── Screenshot (fixed: saves to file) ─────────────────────────────────────────


async def _screenshot(selector: str | None = None, quality: int = 80) -> str:
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

    result = await _cdp_call("Page.captureScreenshot", params)
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
    with open(filepath, "wb") as f:
        f.write(img_bytes)

    size_kb = len(img_bytes) / 1024
    url = await _evaluate("window.location.href")
    return (
        f"Screenshot saved.\n"
        f"File: {filepath}\n"
        f"Size: {size_kb:.1f} KB\n"
        f"Format: {fmt_param}\n"
        f"URL: {url}\n"
        f"(Use the Read tool to view the image at the path above)"
    )


# ── Annotated Screenshot (#10) ────────────────────────────────────────────────


async def _screenshot_annotated(
    max_elements: int = 80,
    min_size: int = 8,
    quality: int = 80,
) -> str:
    """Take a screenshot with interactive elements annotated.

    Each clickable element gets:
      - a colored bounding box
      - a numbered label
      - a small tag with its text / role / type

    Returns the image path plus a JSON manifest of all annotated elements
    so the caller can map "element #5" to its actual DOM node.

    Args:
        max_elements: Cap to avoid overwhelming the image.
        min_size: Skip boxes smaller than this (px) to reduce noise.
        quality: JPEG quality (1-100, 100+ for PNG).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return "[ERROR] PIL (Pillow) is required. Install with: pip install Pillow"

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    quality = min(max(quality, 1), 100)
    fmt_param = "png" if quality >= 100 else "jpeg"

    elements_js = """
    (() => {
        const SEL = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="menuitem"], [role="tab"], [onclick]';
        const nodes = document.querySelectorAll(SEL);
        const results = [];
        const vw = window.innerWidth;
        const vh = window.innerHeight;
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
            const text = (el.innerText || el.value || el.placeholder || el.title || el.getAttribute('aria-label') || '').trim().substring(0, 60);
            const role = el.getAttribute('role') || '';
            const href = (el.getAttribute && el.getAttribute('href')) || '';
            const sel = (() => {
                if (el.id) return '#' + el.id;
                if (el.name) return tag + '[name="' + el.name + '"]';
                if (el.className && typeof el.className === 'string') {
                    const c = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
                    if (c) return tag + '.' + c;
                }
                return tag;
            })();
            results.push({
                i: idx++,
                tag, type, text, role, href, sel,
                x: r.x, y: r.y, w: r.width, h: r.height
            });
            if (idx >= MAX) break;
        }
        return JSON.stringify({viewport: {w: vw, h: vh}, elements: results});
    })()
    """.replace("MAX", str(max_elements)).replace("MIN", str(min_size))

    manifest_str = await _evaluate(elements_js)
    try:
        manifest = json.loads(manifest_str)
    except Exception as e:
        return f"[ERROR] Failed to parse element manifest: {e}\nRaw: {manifest_str[:500]}"

    viewport = manifest.get("viewport", {})
    elements = manifest.get("elements", [])

    # Capture the screenshot
    params = {"format": fmt_param, "quality": quality, "captureBeyondViewport": True}
    result = await _cdp_call("Page.captureScreenshot", params)
    if "error" in result or "data" not in result:
        return f"[ERROR] Screenshot failed: {result.get('error', 'no data')}"

    img_bytes = base64.b64decode(result["data"])

    timestamp = int(time.time() * 1000)
    raw_path = os.path.join(SCREENSHOT_DIR, f"screenshot_{timestamp}.{fmt_param}")
    with open(raw_path, "wb") as f:
        f.write(img_bytes)

    # Annotate
    img = Image.open(raw_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Try to load a font
    font = None
    label_font = None
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, 14)
                label_font = ImageFont.truetype(path, 11)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
        label_font = font

    color_palette = [
        (255, 0, 0, 220),    # red
        (0, 128, 255, 220),  # blue
        (0, 200, 0, 220),    # green
        (255, 128, 0, 220),  # orange
        (180, 0, 255, 220),  # purple
        (0, 200, 200, 220),  # teal
    ]

    for el in elements:
        color = color_palette[el["i"] % len(color_palette)]
        x, y, w, h = el["x"], el["y"], el["w"], el["h"]
        # Draw rectangle
        draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
        # Numbered label background
        label_text = f"#{el['i']}"
        try:
            tb = draw.textbbox((0, 0), label_text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
        except AttributeError:
            tw, th = 20, 16
        label_x = x
        label_y = max(0, y - th - 4)
        draw.rectangle([label_x, label_y, label_x + tw + 6, label_y + th + 4], fill=color)
        draw.text((label_x + 3, label_y + 2), label_text, fill=(255, 255, 255, 255), font=font)

    img = Image.alpha_composite(img, overlay)
    annotated_path = os.path.join(SCREENSHOT_DIR, f"annotated_{timestamp}.png")
    img.convert("RGB").save(annotated_path, "PNG", optimize=True)

    # Write manifest JSON next to image
    manifest_path = os.path.join(SCREENSHOT_DIR, f"annotated_{timestamp}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(annotated_path) / 1024
    url = await _evaluate("window.location.href")
    summary_lines = [
        f"Annotated screenshot saved.",
        f"Image: {annotated_path} ({size_kb:.1f} KB)",
        f"Manifest: {manifest_path}",
        f"Elements annotated: {len(elements)} / viewport: {viewport.get('w')}x{viewport.get('h')}",
        f"URL: {url}",
        f"",
        f"Manifest format: array of {{i, tag, type, text, role, sel, x, y, w, h}}",
        f"Use 'sel' to target an element (e.g. document.querySelector('<sel>'))",
        f"Use cdp_click_xy(x + w/2, y + h/2) to click an element by its number.",
    ]
    return "\n".join(summary_lines)


# ── Profile & Session ─────────────────────────────────────────────────────────


async def _cookies_get(domain: str | None = None) -> str:
    params = {}
    if domain:
        urls = []
        for scheme in ("https://", "http://"):
            urls.append(f"{scheme}{domain}")
            urls.append(f"{scheme}{domain}/*")
        params["urls"] = urls
    result = await _cdp_call("Network.getCookies", params)
    cookies = result.get("cookies", [])
    if not cookies:
        return f"No cookies found" + (f" for domain: {domain}" if domain else "")
    lines = [f"{len(cookies)} cookies" + (f" for {domain}:" if domain else ":")]
    for c in cookies:
        secure_flag = " [secure]" if c.get("secure") else ""
        http_only = " [httpOnly]" if c.get("httpOnly") else ""
        session = " [session]" if c.get("session") else ""
        expires = ""
        if c.get("expires") and c["expires"] > 0:
            expires = f" expires={time.strftime('%Y-%m-%d', time.gmtime(c['expires']))}"
        lines.append(
            f"  {c.get('name', '?')}={c.get('value', '')[:40]}"
            f"  domain={c.get('domain', '')}  path={c.get('path', '/')}"
            f"{secure_flag}{http_only}{session}{expires}"
        )
    return "\n".join(lines)


async def _login_check(
    url: str,
    logged_in_selector: str | None = None,
    logged_out_selector: str | None = None,
) -> str:
    await _cdp_call("Page.navigate", {"url": url})
    await asyncio.sleep(2.5)

    final_url = await _evaluate("window.location.href")
    title = await _evaluate("document.title")

    from urllib.parse import urlparse
    domain = urlparse(url).netloc or urlparse("https://" + url).netloc
    cookies_info = await _cookies_get(domain)

    page_snippet = await _get_text("body", 800)

    selector_results = []
    if logged_in_selector:
        found = await _evaluate(
            f"document.querySelector({json.dumps(logged_in_selector)}) ? 'found' : 'not found'"
        )
        selector_results.append(f"  logged_in_selector '{logged_in_selector}': {found}")
    if logged_out_selector:
        found = await _evaluate(
            f"document.querySelector({json.dumps(logged_out_selector)}) ? 'found' : 'not found'"
        )
        selector_results.append(f"  logged_out_selector '{logged_out_selector}': {found}")

    lines = [
        f"Login check for: {url}",
        f"Final URL:   {final_url}",
        f"Title:       {title}",
        f"",
        cookies_info,
        f"",
        f"Page snippet (first 800 chars):",
        page_snippet,
    ]
    if selector_results:
        lines.append(f"\nSelector checks:")
        lines.extend(selector_results)

    return "\n".join(lines)


# ── Ready & Wait ──────────────────────────────────────────────────────────────


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


# ── Tab Management ────────────────────────────────────────────────────────────


async def _new_tab(url: str = "about:blank") -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{CDP_BASE}/json/version")
        browser_ws = r.json().get("webSocketDebuggerUrl", "")
    if not browser_ws:
        return "[ERROR] Cannot connect to browser"
    async with websockets.connect(browser_ws) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": url}}))
        resp = await ws.recv()
        result = json.loads(resp)
    target_id = result.get("result", {}).get("targetId", "")
    return f"New tab created: {target_id}\nURL: {url}"


async def _list_tabs() -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_BASE}/json/list")
        targets: list[dict] = resp.json()
    pages = [(i, t) for i, t in enumerate(targets) if t.get("type") == "page"]
    lines = []
    for i, t in pages:
        title = (t.get("title", "") or "")[:60]
        url = (t.get("url", "") or "")[:80]
        lines.append(f"[{i}] {title} — {url}")
    if not lines:
        return "No open tabs"
    return "\n".join(lines)


async def _switch_tab(index: int | None = None, match: str | None = None) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CDP_BASE}/json/list")
        targets: list[dict] = resp.json()

    pages = [t for t in targets if t.get("type") == "page"]
    target = None

    if index is not None and 0 <= index < len(pages):
        target = pages[index]
    elif match:
        match_lower = match.lower()
        for p in pages:
            if match_lower in (p.get("title", "") + p.get("url", "")).lower():
                target = p
                break

    if target is None:
        return f"[ERROR] Tab not found. Available:\n{await _list_tabs()}"

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CDP_BASE}/json/version")
            browser_ws = r.json().get("webSocketDebuggerUrl", "")
        if browser_ws:
            async with websockets.connect(browser_ws) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Target.activateTarget", "params": {"targetId": target["id"]}}))
                await ws.recv()
    except Exception:
        pass

    return f"Switched to: [{target.get('title', '')}] {target.get('url', '')}"


# ── MCP Server Definition ─────────────────────────────────────────────────────

server = Server("cdp-browser-enhanced")

TOOLS = [
    # Navigation & Page Info
    Tool(
        name="cdp_navigate",
        description="Navigate the browser to a URL. Returns page title and URL after load.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to navigate to"},
                "wait_ms": {"type": "integer", "description": "Extra wait time after navigation in ms (default 2000)", "default": 2000},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="cdp_page_info",
        description="Get current page info: URL, title, and readyState in one call.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # Reading
    Tool(
        name="cdp_get_text",
        description="Get visible text content of the current page. Returns trimmed plain text (max first 8000 chars).",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to extract text from (default: body)", "default": "body"},
                "max_length": {"type": "integer", "description": "Max chars to return (default 8000)", "default": 8000},
            },
        },
    ),
    Tool(
        name="cdp_get_links",
        description="List all links on the page with href and visible text. Returns JSON array.",
        inputSchema={
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max number of links (default 50)", "default": 50},
                "filter": {"type": "string", "description": "Text filter — only links whose text contains this string"},
            },
        },
    ),
    Tool(
        name="cdp_get_form_elements",
        description="List all form elements (input, select, textarea, button) on the page with their attributes.",
        inputSchema={
            "type": "object",
            "properties": {
                "form_selector": {"type": "string", "description": "CSS selector to limit to a specific form (default: all forms)"},
            },
        },
    ),
    # Interaction
    Tool(
        name="cdp_click",
        description="Click an element on the page by text content or CSS selector.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text of the element to click (fuzzy match). Only used if selector is not provided."},
                "selector": {"type": "string", "description": "CSS selector. Prefer this over text when possible."},
                "index": {"type": "integer", "description": "If multiple elements match text, click the Nth (0-based, default 0)", "default": 0},
            },
        },
    ),
    Tool(
        name="cdp_fill",
        description="Fill an input/textarea field. Supports name, id, CSS selector, or placeholder matching.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Value to match: name, id, CSS selector, or placeholder text"},
                "value": {"type": "string", "description": "Text value to fill"},
                "by": {"type": "string", "enum": ["auto", "name", "id", "selector", "placeholder"], "description": "Match mode (default auto tries all)", "default": "auto"},
            },
            "required": ["target", "value"],
        },
    ),
    Tool(
        name="cdp_select",
        description="Select an option in a <select> dropdown by value or display text.",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "CSS selector, name, or id of the <select> element"},
                "value": {"type": "string", "description": "Option value attribute to select"},
                "label": {"type": "string", "description": "Option display text to select (used if value not provided)"},
            },
            "required": ["target"],
        },
    ),
    Tool(
        name="cdp_scroll",
        description="Scroll the page. Can scroll by pixels, to bottom, or to a specific element.",
        inputSchema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": ["bottom", "top"], "description": "Scroll to bottom or top of page"},
                "pixels": {"type": "integer", "description": "Scroll down by this many pixels"},
                "selector": {"type": "string", "description": "Scroll this element into view"},
            },
        },
    ),
    # Screenshot (fixed)
    Tool(
        name="cdp_screenshot",
        description="Take a screenshot of the current page and save to a file. Returns the file path — use the Read tool to view the image. Only works if your model has vision/image-reading capability.",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to screenshot a specific element (default: full page)"},
                "quality": {"type": "integer", "description": "JPEG quality 1-100, or 100+ for PNG (default 80)", "default": 80},
            },
        },
    ),
    Tool(
        name="cdp_screenshot_annotated",
        description="Take a screenshot with every interactive element (links, buttons, inputs, etc.) outlined in colored boxes and labeled with a number. Also writes a JSON manifest mapping each #N to its DOM info (tag, text, role, selector, x/y/w/h). Use this to decide where to click visually, then call cdp_click_xy(x + w/2, y + h/2).",
        inputSchema={
            "type": "object",
            "properties": {
                "max_elements": {"type": "integer", "description": "Cap on number of elements to annotate (default 80)", "default": 80},
                "min_size": {"type": "integer", "description": "Skip boxes smaller than this many pixels (default 8)", "default": 8},
                "quality": {"type": "integer", "description": "JPEG quality 1-100, 100+ for PNG (default 80)", "default": 80},
            },
        },
    ),
    Tool(
        name="cdp_click_xy",
        description="Click at viewport coordinates (CSS pixels) using real mouse events (Input.dispatchMouseEvent). More reliable than elementFromPoint().click() for SPAs that listen to native mouse events.",
        inputSchema={
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X coordinate in CSS pixels"},
                "y": {"type": "number", "description": "Y coordinate in CSS pixels"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default: left)", "default": "left"},
                "click_count": {"type": "integer", "description": "Click count: 1 for single, 2 for double (default 1)", "default": 1},
                "delay_ms": {"type": "integer", "description": "Delay between mousedown and mouseup in ms (default 50)", "default": 50},
            },
            "required": ["x", "y"],
        },
    ),
    Tool(
        name="cdp_wait_for",
        description="Smart wait: poll until a CSS selector or visible text appears/disappears or matches a desired state. More efficient than blind cdp_wait.",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to wait for"},
                "text": {"type": "string", "description": "Visible text to wait for (substring match). Use when selector is unstable."},
                "state": {"type": "string", "enum": ["visible", "hidden", "attached", "gone"], "description": "Target state (default: visible)", "default": "visible"},
                "timeout_ms": {"type": "integer", "description": "Max wait time in ms (default 5000)", "default": 5000},
                "poll_ms": {"type": "integer", "description": "Polling interval in ms (default 200)", "default": 200},
            },
        },
    ),
    # Session & Profile
    Tool(
        name="cdp_cookies_get",
        description="Get cookies stored in the browser for a given domain. Use to check if the user is logged into a site. Returns cookie names, domains, and expiry info.",
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain to get cookies for (e.g. 'github.com'). If omitted, returns all cookies."},
            },
        },
    ),
    Tool(
        name="cdp_login_check",
        description="Navigate to a URL and check if the user appears to be logged in. Returns final URL (check for redirect to login page), cookies for the domain, page snippet, and optional selector checks. Use this before attempting authenticated actions.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to check login status on"},
                "logged_in_selector": {"type": "string", "description": "CSS selector that should exist if logged in (e.g. '.user-avatar', '.dashboard')"},
                "logged_out_selector": {"type": "string", "description": "CSS selector that should exist if logged out (e.g. 'a[href*=\"login\"]')"},
            },
            "required": ["url"],
        },
    ),
    # Ready / Wait
    Tool(
        name="cdp_ready",
        description="Wait for the page to finish loading (document.readyState === 'complete'). Polls until ready or timeout. Use after navigate instead of blind cdp_wait.",
        inputSchema={
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "description": "Max milliseconds to wait (default 5000)", "default": 5000},
            },
        },
    ),
    Tool(
        name="cdp_wait",
        description="Wait/pause for a specified duration. Useful after navigation or before reading newly loaded content.",
        inputSchema={
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "description": "Wait time in milliseconds (default 1000)", "default": 1000},
            },
        },
    ),
    # Console
    Tool(
        name="cdp_console",
        description="Capture and retrieve browser console messages (log, warn, error, info, debug). First call enables capture; subsequent calls return captured messages. Useful for debugging page errors or JS behavior.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # Storage
    Tool(
        name="cdp_storage",
        description="Read or write localStorage/sessionStorage. Many SPAs store auth tokens here. Use 'local' or 'session' for the location parameter.",
        inputSchema={
            "type": "object",
            "properties": {
                "location": {"type": "string", "enum": ["local", "session"], "description": "localStorage or sessionStorage"},
                "key": {"type": "string", "description": "Storage key to read/write"},
                "value": {"type": "string", "description": "Value to set (omit to read/get)"},
            },
            "required": ["location", "key"],
        },
    ),
    # Tab Management
    Tool(
        name="cdp_new_tab",
        description="Create a new browser tab and optionally navigate to a URL.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate the new tab to (default: about:blank)", "default": "about:blank"},
            },
        },
    ),
    Tool(
        name="cdp_list_tabs",
        description="List all open browser tabs with URL and title.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="cdp_switch_tab",
        description="Switch to a tab by index (0-based, from cdp_list_tabs) or URL/title match.",
        inputSchema={
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Tab index from cdp_list_tabs"},
                "match": {"type": "string", "description": "Switch to tab whose URL or title contains this string"},
            },
        },
    ),
    # Utility (escape hatch)
    Tool(
        name="cdp_evaluate",
        description="Execute arbitrary JavaScript in the page and return the result. Escape hatch for operations not covered by other tools. Use for: form submission, clicking obscured elements, extracting structured data, or DOM manipulation.",
        inputSchema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript code to execute. Use IIFE (() => {...}) for multi-line. Result is returned as string."},
                "return_result": {"type": "boolean", "description": "If false, just execute with no return value (default true)", "default": True},
            },
            "required": ["code"],
        },
    ),
]


# ── MCP Entry Points ──────────────────────────────────────────────────────────


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
        # Navigation & Page Info
        case "cdp_navigate":
            return _ok(await _navigate(args.get("url", ""), args.get("wait_ms", 2000)))
        case "cdp_page_info":
            return _ok(await _page_info())
        # Reading
        case "cdp_get_text":
            return _ok(await _get_text(args.get("selector", "body"), args.get("max_length", 8000)))
        case "cdp_get_links":
            return _ok(await _get_links(args.get("max_results", 50), args.get("filter")))
        case "cdp_get_form_elements":
            return _ok(await _get_form_elements(args.get("form_selector")))
        # Interaction
        case "cdp_click":
            return _ok(await _click(args.get("text"), args.get("selector"), args.get("index", 0)))
        case "cdp_fill":
            return _ok(await _fill(args["target"], args["value"], args.get("by", "auto")))
        case "cdp_select":
            return _ok(await _select(args["target"], args.get("value"), args.get("label")))
        case "cdp_scroll":
            return _ok(await _scroll(args.get("to"), args.get("pixels"), args.get("selector")))
        # Screenshot
        case "cdp_screenshot":
            return _ok(await _screenshot(args.get("selector"), args.get("quality", 80)))
        case "cdp_screenshot_annotated":
            return _ok(await _screenshot_annotated(
                args.get("max_elements", 80),
                args.get("min_size", 8),
                args.get("quality", 80),
            ))
        case "cdp_click_xy":
            return _ok(await _click_xy(
                args["x"], args["y"],
                args.get("button", "left"),
                args.get("click_count", 1),
                args.get("delay_ms", 50),
            ))
        case "cdp_wait_for":
            return _ok(await _wait_for(
                args.get("selector"),
                args.get("text"),
                args.get("state", "visible"),
                args.get("timeout_ms", 5000),
                args.get("poll_ms", 200),
            ))
        # Session & Profile
        case "cdp_cookies_get":
            return _ok(await _cookies_get(args.get("domain")))
        case "cdp_login_check":
            return _ok(await _login_check(
                args["url"],
                args.get("logged_in_selector"),
                args.get("logged_out_selector"),
            ))
        # Ready / Wait
        case "cdp_ready":
            return _ok(await _ready(args.get("timeout_ms", 5000)))
        case "cdp_wait":
            ms = args.get("ms", 1000)
            await asyncio.sleep(ms / 1000)
            return _ok(f"Waited {ms}ms")
        # Console
        case "cdp_console":
            return _ok(await _console())
        # Storage
        case "cdp_storage":
            return _ok(await _storage(args["location"], args["key"], args.get("value")))
        # Tab Management
        case "cdp_new_tab":
            return _ok(await _new_tab(args.get("url", "about:blank")))
        case "cdp_list_tabs":
            return _ok(await _list_tabs())
        case "cdp_switch_tab":
            return _ok(await _switch_tab(args.get("index"), args.get("match")))
        # Utility
        case "cdp_evaluate":
            result = await _evaluate(args["code"])
            return _ok(result)
        case _:
            return _err(f"Unknown tool: {name}")


# ── Main ──────────────────────────────────────────────────────────────────────


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

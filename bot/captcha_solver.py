"""
AI captcha solver cho fly88h.com — hỗ trợ 2 loại:
  1. "Bấm theo thứ tự" (click-order)
  2. "Lướt hình" (slide/drag)

Quy trình:
  1. Dùng JS tìm container captcha → lấy bounding rect
  2. Chụp RIÊNG phần tử đó (ảnh nhỏ, rõ hơn toàn trang)
  3. Gửi AI → nhận tọa độ tương đối với ảnh đó
  4. Cộng offset container → click/kéo đúng vị trí trên trang
"""
import os, base64, json, re, time, logging
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
AI_PROVIDER    = os.environ.get("AI_CAPTCHA_PROVIDER", "openai")

PROMPT_CLICK = """This is a CROPPED screenshot of a Vietnamese image captcha widget ("Chọn theo thứ tự này" = Select in this order).

At the TOP of this image: a row of small icon thumbnails showing the REQUIRED click order (left=first, right=last).
In the MAIN image area below: the same icons appear scattered at larger size on a background photo.

Your job:
1. Identify each icon in the top row (left to right).
2. Find where that same icon appears in the main image area.
3. Return the CENTER pixel coordinates of each icon in the main image, in click order.

Return ONLY valid JSON — no extra text:
{"type": "click", "coords": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}

Coordinates are relative to the TOP-LEFT of THIS cropped image.
"""

PROMPT_SLIDE = """This is a CROPPED screenshot of a Vietnamese slide/drag captcha widget ("Lướt hình").

The image shows:
- A background image with a GAP (dark/missing area) somewhere
- A small floating piece that needs to fit into the gap
- A horizontal slider bar at the bottom with a draggable handle

Your job: estimate how many pixels the slider handle needs to be dragged to the RIGHT to align the piece with the gap.

Return ONLY valid JSON:
{"type": "slide", "distance": <number_of_pixels>}
"""

PROMPT_DETECT = """This is a screenshot of a Vietnamese website. Look for a captcha widget on the page.

Determine which type of captcha is visible:
- "click" = shows "Chọn theo thứ tự này" with icons to click in order
- "slide" = shows a puzzle piece to drag / slider bar ("Lướt hình" or drag-puzzle)
- "none"  = no captcha visible

Return ONLY valid JSON:
{"type": "click"} OR {"type": "slide"} OR {"type": "none"}
"""


# ── AI helpers ──────────────────────────────────────────────────────────────
def _call_openai(image_b64: str, prompt: str) -> str:
    import urllib.request
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [
            {"type": "text",      "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}],
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def _call_gemini(image_b64: str, prompt: str) -> str:
    import urllib.request
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
    )
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inlineData": {"mimeType": "image/png", "data": image_b64}},
    ]}], "generationConfig": {"maxOutputTokens": 200}}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        resp = json.loads(r.read())
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


def _ask_ai(image_b64: str, prompt: str) -> dict:
    if AI_PROVIDER == "gemini" and GOOGLE_API_KEY:
        raw = _call_gemini(image_b64, prompt)
    elif OPENAI_API_KEY:
        raw = _call_openai(image_b64, prompt)
    else:
        raise RuntimeError("Chưa có OPENAI_API_KEY hoặc GOOGLE_API_KEY")
    logger.info(f"AI raw: {raw[:200]}")
    clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    m = re.search(r'\{[^{}]*\}', clean, re.DOTALL)
    if not m:
        return {"type": "none"}
    try:
        return json.loads(m.group())
    except Exception:
        return {"type": "none"}


# ── Tìm captcha container qua JS → trả về WebElement + rect ─────────────────
def _find_captcha_element(driver):
    """
    Dùng JS tìm container chứa captcha.
    Trả về (WebElement, rect_dict) hoặc (None, None).
    """
    el = driver.execute_script(r"""
        function findContainer(startEl, minW, minH) {
            let el = startEl;
            for (let i = 0; i < 10; i++) {
                if (!el || !el.parentElement) break;
                el = el.parentElement;
                let r = el.getBoundingClientRect();
                if (r.width >= minW && r.height >= minH) return el;
            }
            return null;
        }

        // Loại 1: click-order — tìm text "Chọn theo thứ tự"
        let walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while (node = walker.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes('Chọn theo thứ tự')) {
                let c = findContainer(node.parentElement, 180, 180);
                if (c) return c;
            }
        }

        // Loại 2: slide — tìm class geetest hoặc slide
        let candidates = [
            ...document.querySelectorAll('[class*="geetest"]'),
            ...document.querySelectorAll('[class*="captcha"]'),
            ...document.querySelectorAll('[class*="slide-captcha"]'),
            ...document.querySelectorAll('[class*="slideCaptcha"]'),
        ];
        for (let c of candidates) {
            let r = c.getBoundingClientRect();
            if (r.width > 200 && r.height > 100 && r.top > 0) return c;
        }
        return null;
    """)

    if el is None:
        return None, None

    rect = driver.execute_script(
        "let r=arguments[0].getBoundingClientRect(); "
        "return {x:r.left,y:r.top,w:r.width,h:r.height};", el
    )
    return el, rect


# ── Chụp ảnh captcha (riêng phần tử) ────────────────────────────────────────
def _screenshot_element(driver, el, rect) -> str:
    """Chụp phần tử captcha, trả về base64 PNG."""
    try:
        png = el.screenshot_as_png
        if png and len(png) > 500:
            return base64.b64encode(png).decode()
    except Exception as e:
        logger.warning(f"screenshot_as_png thất bại ({e}), thử crop từ full page")

    # Fallback: crop từ full-page screenshot
    try:
        from PIL import Image
        from io import BytesIO
        full = driver.get_screenshot_as_png()
        img  = Image.open(BytesIO(full))
        # Tỉ lệ device pixel ratio (Retina = 2x)
        dpr  = driver.execute_script("return window.devicePixelRatio || 1;")
        box  = (
            int(rect["x"] * dpr),
            int(rect["y"] * dpr),
            int((rect["x"] + rect["w"]) * dpr),
            int((rect["y"] + rect["h"]) * dpr),
        )
        cropped = img.crop(box)
        buf = BytesIO()
        cropped.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Crop PIL thất bại: {e}")

    # Last resort: full page
    return base64.b64encode(driver.get_screenshot_as_png()).decode()


# ── Giải click-order ─────────────────────────────────────────────────────────
def _solve_click(driver, coords, rect) -> bool:
    if not coords:
        return False
    for idx, (cx, cy) in enumerate(coords):
        # Tọa độ trong ảnh captcha → tọa độ viewport trang
        px = int(rect["x"] + cx)
        py = int(rect["y"] + cy)
        logger.info(f"Click {idx+1}/{len(coords)}: captcha({cx},{cy}) → page({px},{py})")
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e)e.dispatchEvent(new MouseEvent('click',"
            "{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));",
            px, py,
        )
        time.sleep(0.7)
    time.sleep(1)
    _press_ok(driver)
    return True


# ── Giải slide ───────────────────────────────────────────────────────────────
def _solve_slide(driver, distance: int) -> bool:
    handle = None
    for sel in [
        "[class*='geetest_slider_button']", "[class*='slide_button']",
        "[class*='slider_btn']",            "[class*='drag_button']",
        "[class*='captcha'] [class*='btn']",
        "//div[contains(@class,'slide')]//div[contains(@class,'handle')]",
        "//div[contains(@class,'slider')]//div[1]",
    ]:
        try:
            fn = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            e = driver.find_element(fn, sel)
            if e.is_displayed():
                handle = e
                break
        except Exception:
            continue

    if not handle:
        logger.warning("Không tìm thấy slider handle")
        return False

    steps = max(distance // 8, 6)
    px_per_step = distance / steps
    ac = ActionChains(driver)
    ac.click_and_hold(handle)
    for _ in range(steps):
        ac.move_by_offset(int(px_per_step), 0)
        ac.pause(0.025)
    ac.release().perform()
    time.sleep(1.5)
    return True


def _press_ok(driver):
    for xpath in [
        "//*[normalize-space(text())='OK']",
        "//*[normalize-space(text())='ok']",
        "//*[contains(@class,'submit')]",
        "//*[contains(@class,'geetest_submit')]",
    ]:
        try:
            e = driver.find_element(By.XPATH, xpath)
            if e.is_displayed():
                e.click()
                logger.info(f"OK clicked: {xpath}")
                time.sleep(1)
                return
        except Exception:
            continue


# ── Hàm công khai ────────────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    """
    Phát hiện loại captcha, giải và trả về True nếu thành công.
    """
    time.sleep(2)   # Chờ widget render

    # Bước 1: Tìm container captcha
    captcha_el, rect = _find_captcha_element(driver)

    if captcha_el and rect:
        logger.info(f"Container tìm thấy: x={rect['x']:.0f} y={rect['y']:.0f} "
                    f"w={rect['w']:.0f} h={rect['h']:.0f}")
        img_b64 = _screenshot_element(driver, captcha_el, rect)
    else:
        logger.warning("Không tìm thấy container → chụp toàn trang")
        img_b64  = base64.b64encode(driver.get_screenshot_as_png()).decode()
        rect     = {"x": 0, "y": 0, "w": 1280, "h": 800}   # toàn trang

    # Bước 2: Xác định loại captcha
    detect = _ask_ai(img_b64, PROMPT_DETECT)
    ctype  = detect.get("type", "none")
    logger.info(f"Loại captcha phát hiện: {ctype}")

    if ctype == "none":
        return False

    # Bước 3: Phân tích chi tiết
    if ctype == "click":
        result = _ask_ai(img_b64, PROMPT_CLICK)
        coords = result.get("coords", [])
        logger.info(f"Tọa độ click (trong ảnh captcha): {coords}")
        return _solve_click(driver, coords, rect)

    elif ctype == "slide":
        result = _ask_ai(img_b64, PROMPT_SLIDE)
        dist   = int(result.get("distance", 120))
        logger.info(f"Slide distance: {dist}px")
        return _solve_slide(driver, dist)

    return False

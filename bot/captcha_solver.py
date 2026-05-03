"""
AI captcha solver cho fly88h.com — hỗ trợ 2 loại:
  1. "Bấm theo thứ tự" (click-order) — click các icon theo thứ tự chỉ định
  2. "Lướt hình" (slide/drag)        — kéo thanh slider vào đúng vị trí

Cấu trúc modal captcha (cố định, tương đối trong modal):
  - Dòng "Chọn theo thứ tự này" + icon sequence: ~top 13% modal
  - Vùng ảnh chính (chứa icon cần click): ~13%–65% chiều cao modal
  - Nút OK: ~74% chiều cao modal, giữa ngang

Quy trình:
  1. JS tìm modal chứa "Chọn theo thứ tự" → getBoundingClientRect
  2. Pillow crop ảnh vùng modal (nhỏ, rõ hơn toàn trang)
  3. Gửi AI → tọa độ tương đối trong ảnh modal
  4. Cộng (modal.x, modal.y) → click đúng trên viewport
"""
import os, base64, json, re, time, logging
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
AI_PROVIDER    = os.environ.get("AI_CAPTCHA_PROVIDER", "openai")

# ─────────────────────────────────────────────────────
#  Prompts
# ─────────────────────────────────────────────────────
PROMPT_CLICK = """This image shows a Vietnamese image captcha widget.

STRUCTURE of this image (from top to bottom):
- TOP STRIP (~top 15%): "Chọn theo thứ tự này:" followed by N small icon boxes showing the REQUIRED CLICK ORDER (left-to-right = first to last).
- MAIN IMAGE AREA (~15%–70%): a background photo with the SAME N icons scattered around at larger size.
- BOTTOM: an "OK" button.

YOUR TASK:
1. Read the icon sequence in the TOP STRIP (left = click first).
2. For each icon in that sequence, find its CENTER position in the MAIN IMAGE AREA.
3. Return coordinates relative to the TOP-LEFT of THIS entire image.

Return ONLY valid JSON — no other text:
{"type":"click","coords":[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}
"""

PROMPT_SLIDE = """This image shows a Vietnamese slide/drag captcha widget ("Lướt hình").

STRUCTURE:
- A background image with a DARK GAP/HOLE somewhere.
- A floating puzzle piece that needs to fit into the gap.
- A horizontal SLIDER BAR at the bottom with a draggable handle on the left.

YOUR TASK: Estimate how many pixels the slider handle needs to be dragged RIGHT to align the piece with the gap.

Return ONLY valid JSON — no other text:
{"type":"slide","distance":<pixels>}
"""

PROMPT_DETECT = """Look at this screenshot of a website. Is there a captcha widget visible?

Types:
- "click" → shows "Chọn theo thứ tự này" text with icons and a photo
- "slide"  → shows a drag puzzle / slider bar ("Lướt hình")
- "none"   → no captcha visible

Return ONLY valid JSON:
{"type":"click"} or {"type":"slide"} or {"type":"none"}
"""


# ─────────────────────────────────────────────────────
#  AI helpers
# ─────────────────────────────────────────────────────
def _call_openai(b64: str, prompt: str) -> str:
    import urllib.request
    payload = {"model": "gpt-4o", "max_tokens": 200,
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
               ]}]}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def _call_gemini(b64: str, prompt: str) -> str:
    import urllib.request
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inlineData": {"mimeType": "image/png", "data": b64}},
    ]}], "generationConfig": {"maxOutputTokens": 200}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        resp = json.loads(r.read())
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


def _ai(b64: str, prompt: str) -> dict:
    if AI_PROVIDER == "gemini" and GOOGLE_API_KEY:
        raw = _call_gemini(b64, prompt)
    elif OPENAI_API_KEY:
        raw = _call_openai(b64, prompt)
    else:
        raise RuntimeError("Chưa có OPENAI_API_KEY hoặc GOOGLE_API_KEY")
    logger.info(f"AI → {raw[:200]}")
    clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
    m = re.search(r'\{[^{}]*\}', clean, re.DOTALL)
    if not m:
        return {"type": "none"}
    try:
        return json.loads(m.group())
    except Exception:
        return {"type": "none"}


# ─────────────────────────────────────────────────────
#  Tìm captcha modal qua JS
# ─────────────────────────────────────────────────────
def _get_modal_rect(driver) -> dict | None:
    """
    Tìm container modal captcha, trả về rect {x, y, w, h} trong viewport CSS pixels.
    Cố gắng tìm bằng text "Chọn theo thứ tự", rồi fallback bằng class.
    """
    rect = driver.execute_script(r"""
        function getRect(el, minW, minH) {
            let cur = el;
            for (let i = 0; i < 12; i++) {
                if (!cur || !cur.parentElement) break;
                cur = cur.parentElement;
                let r = cur.getBoundingClientRect();
                if (r.width >= minW && r.height >= minH && r.top >= 0) return r;
            }
            return null;
        }

        // 1. Tìm text "Chọn theo thứ tự"
        let tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while (node = tw.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes('Chọn theo thứ tự')) {
                let r = getRect(node.parentElement, 200, 200);
                if (r) return {x: r.left, y: r.top, w: r.width, h: r.height};
            }
        }

        // 2. Fallback: class captcha / geetest
        for (let sel of ['[class*="geetest"]','[class*="captcha"]','[class*="verify"]']) {
            for (let el of document.querySelectorAll(sel)) {
                let r = el.getBoundingClientRect();
                if (r.width > 200 && r.height > 200 && r.top > 0)
                    return {x: r.left, y: r.top, w: r.width, h: r.height};
            }
        }
        return null;
    """)
    if rect and rect.get("w", 0) > 100:
        logger.info(f"Modal rect: {rect}")
        return rect
    return None


# ─────────────────────────────────────────────────────
#  Crop modal từ screenshot bằng Pillow
# ─────────────────────────────────────────────────────
def _crop_modal(driver, rect: dict) -> str:
    """
    Chụp toàn trang → crop vùng modal → trả base64 PNG.
    Xử lý cả devicePixelRatio (Retina = 2x).
    """
    full_png = driver.get_screenshot_as_png()
    img = Image.open(BytesIO(full_png))

    # Tính tỷ lệ screenshot / viewport (DPR)
    vp_w  = driver.execute_script("return window.innerWidth;")
    dpr   = img.width / vp_w if vp_w else 1.0
    logger.info(f"Screenshot {img.width}×{img.height}, viewport {vp_w}, DPR={dpr:.2f}")

    x1 = max(0, int(rect["x"] * dpr))
    y1 = max(0, int(rect["y"] * dpr))
    x2 = min(img.width,  int((rect["x"] + rect["w"]) * dpr))
    y2 = min(img.height, int((rect["y"] + rect["h"]) * dpr))

    cropped = img.crop((x1, y1, x2, y2))
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ─────────────────────────────────────────────────────
#  Click từng icon theo thứ tự
# ─────────────────────────────────────────────────────
def _do_click(driver, coords: list, modal_rect: dict, img_b64_for_log: str) -> bool:
    if not coords:
        logger.warning("Không có tọa độ click")
        return False

    # Tọa độ AI trả về là tương đối với ảnh modal đã crop
    # Ảnh modal có kích thước (modal_rect["w"], modal_rect["h"]) ở DPR=1
    # (vì chúng ta sẽ chia lại cho DPR khi cần, nhưng Pillow đã crop theo DPR
    # nên tọa độ AI cần được scale ngược lại)
    vp_w  = driver.execute_script("return window.innerWidth;")
    full  = driver.get_screenshot_as_png()
    img_w = Image.open(BytesIO(full)).width
    dpr   = img_w / vp_w if vp_w else 1.0

    for idx, (cx, cy) in enumerate(coords):
        # AI coord (cx, cy) là pixel trong ảnh crop (đã nhân DPR)
        # Chuyển về viewport CSS pixel:  modal.x + cx/dpr
        px = int(modal_rect["x"] + cx / dpr)
        py = int(modal_rect["y"] + cy / dpr)
        logger.info(f"  Click {idx+1}: crop({cx},{cy}) → viewport({px},{py})")
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e)e.dispatchEvent(new MouseEvent('click',"
            "{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));",
            px, py,
        )
        time.sleep(0.8)

    time.sleep(1)
    _press_ok(driver, modal_rect)
    return True


# ─────────────────────────────────────────────────────
#  Kéo slider
# ─────────────────────────────────────────────────────
def _do_slide(driver, distance: int) -> bool:
    handle = None
    for sel in [
        "[class*='geetest_slider_button']", "[class*='slide_button']",
        "[class*='slider_btn']",            "[class*='drag_button']",
        "[class*='slider'] [class*='btn']",
        "//div[contains(@class,'slide')]//div[contains(@class,'handle')]",
    ]:
        try:
            fn = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
            e  = driver.find_element(fn, sel)
            if e.is_displayed():
                handle = e
                break
        except Exception:
            continue

    if not handle:
        logger.warning("Không tìm thấy slider handle")
        return False

    steps = max(distance // 8, 6)
    ppx   = distance / steps
    ac    = ActionChains(driver)
    ac.click_and_hold(handle)
    for _ in range(steps):
        ac.move_by_offset(int(ppx), 0).pause(0.025)
    ac.release().perform()
    time.sleep(1.5)
    return True


# ─────────────────────────────────────────────────────
#  Nhấn OK
# ─────────────────────────────────────────────────────
def _press_ok(driver, modal_rect: dict = None):
    # Thử JS tìm nút OK trong modal
    for xpath in [
        "//*[normalize-space(text())='OK']",
        "//*[normalize-space(text())='ok']",
        "//*[contains(@class,'geetest_submit')]",
        "//*[contains(@class,'captcha_submit')]",
        "//*[contains(@class,'submit')]",
    ]:
        try:
            e = driver.find_element(By.XPATH, xpath)
            if e.is_displayed():
                e.click()
                logger.info(f"OK clicked via: {xpath}")
                time.sleep(1)
                return
        except Exception:
            continue

    # Fallback: click tại vị trí cố định trong modal (74% chiều cao modal)
    if modal_rect:
        px = int(modal_rect["x"] + modal_rect["w"] * 0.49)
        py = int(modal_rect["y"] + modal_rect["h"] * 0.74)
        logger.info(f"OK fallback click tại ({px},{py})")
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e)e.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));",
            px, py,
        )
        time.sleep(1)


# ─────────────────────────────────────────────────────
#  Hàm chính
# ─────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    time.sleep(2)   # Chờ widget render xong

    # Bước 1: Tìm vị trí modal captcha trong viewport
    modal = _get_modal_rect(driver)

    if modal:
        img_b64 = _crop_modal(driver, modal)
        logger.info("Đã crop ảnh modal captcha")
    else:
        logger.warning("Không tìm thấy modal → dùng full page")
        img_b64 = base64.b64encode(driver.get_screenshot_as_png()).decode()
        modal   = {"x": 0, "y": 0, "w": 1280, "h": 800}

    # Bước 2: Phát hiện loại captcha
    det   = _ai(img_b64, PROMPT_DETECT)
    ctype = det.get("type", "none")
    logger.info(f"Loại captcha: {ctype}")

    if ctype == "none":
        return False

    # Bước 3: Giải chi tiết
    if ctype == "click":
        res    = _ai(img_b64, PROMPT_CLICK)
        coords = res.get("coords", [])
        logger.info(f"Tọa độ (trong ảnh modal): {coords}")
        return _do_click(driver, coords, modal, img_b64)

    elif ctype == "slide":
        res  = _ai(img_b64, PROMPT_SLIDE)
        dist = int(res.get("distance", 120))
        logger.info(f"Slide: {dist}px")
        return _do_slide(driver, dist)

    return False

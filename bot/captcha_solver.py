"""
AI-powered captcha solver cho fly88h.com.
Hỗ trợ 2 loại:
  1. "Bấm theo thứ tự" — click các icon theo thứ tự chỉ định
  2. "Lướt hình"       — kéo thanh slider vào đúng vị trí

Cách hoạt động:
  - Chụp toàn trang (không cần tìm container) → gửi AI phân tích
  - AI trả về loại captcha + tọa độ cần tương tác
  - Thực hiện click hoặc kéo
"""
import os
import base64
import logging
import json
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
AI_PROVIDER    = os.environ.get("AI_CAPTCHA_PROVIDER", "openai")

# ── Prompt gửi AI ──────────────────────────────────────────────────────────
PROMPT = """This screenshot is from a Vietnamese gambling website registration page.
A captcha widget is visible. Analyze it and determine which type it is:

TYPE A — "Click in order" (Bấm theo thứ tự):
  - Shows a row of small icon thumbnails at the top labeled "Chọn theo thứ tự này"
  - A main image below with the same icons scattered around
  - Task: click each icon in the order shown at top, then click OK

TYPE B — "Slide puzzle" (Lướt hình / Kéo mảnh):
  - Shows an image with a missing piece (gap)
  - A smaller thumbnail shows the piece to drag
  - A horizontal slider bar at the bottom
  - Task: drag the slider right until the piece fits the gap

Respond with ONLY valid JSON in one of these formats:

For TYPE A:
{"type": "click", "coords": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}

For TYPE B:
{"type": "slide", "distance": <pixels_to_drag_right>}

If no captcha is visible or you cannot determine:
{"type": "none"}

Coordinates are pixel positions in the screenshot. Distance is how many pixels to drag the slider.
Return ONLY the JSON object, no other text.
"""


# ── Gọi AI ─────────────────────────────────────────────────────────────────
def _ask_openai(image_b64: str) -> str:
    import urllib.request
    payload = {
        "model": "gpt-4o",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text",      "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 150,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"].strip()


def _ask_gemini(image_b64: str) -> str:
    import urllib.request
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inlineData": {"mimeType": "image/png", "data": image_b64}},
            ]
        }],
        "generationConfig": {"maxOutputTokens": 150},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        resp = json.loads(r.read())
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


def _ask_ai(image_b64: str) -> dict:
    """Gửi ảnh lên AI và parse kết quả JSON."""
    try:
        if AI_PROVIDER == "gemini" and GOOGLE_API_KEY:
            raw = _ask_gemini(image_b64)
        elif OPENAI_API_KEY:
            raw = _ask_openai(image_b64)
        else:
            raise RuntimeError("Chưa cấu hình OPENAI_API_KEY hoặc GOOGLE_API_KEY")
        logger.info(f"AI trả lời: {raw}")
    except Exception as e:
        logger.error(f"Lỗi gọi AI: {e}")
        return {"type": "error", "msg": str(e)}

    # Parse JSON từ response (có thể bị bọc trong ```json...```)
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        logger.warning(f"Không parse được JSON từ: {raw}")
        return {"type": "none"}
    try:
        return json.loads(match.group())
    except Exception:
        return {"type": "none"}


# ── Thực hiện click theo thứ tự ────────────────────────────────────────────
def _do_click_captcha(driver, coords: list) -> bool:
    if not coords:
        logger.warning("Không có tọa độ để click")
        return False

    actions = ActionChains(driver)
    body = driver.find_element(By.TAG_NAME, "body")

    for idx, (x, y) in enumerate(coords):
        logger.info(f"Click {idx+1}/{len(coords)} tại ({x}, {y})")
        # Dùng JS click tại tọa độ tuyệt đối (tính theo viewport)
        driver.execute_script(
            "document.elementFromPoint(arguments[0], arguments[1]).dispatchEvent("
            "new MouseEvent('click', {bubbles:true, cancelable:true, "
            "clientX:arguments[0], clientY:arguments[1]}));",
            int(x), int(y)
        )
        time.sleep(0.7)

    time.sleep(1)

    # Nhấn OK
    _click_ok(driver)
    return True


# ── Thực hiện kéo slider ────────────────────────────────────────────────────
def _do_slide_captcha(driver, distance: int) -> bool:
    slider_selectors = [
        "[class*='geetest_slider_button']",
        "[class*='slider_btn']",
        "[class*='slider-btn']",
        "[class*='slide_btn']",
        "[class*='captcha_slide']",
        "[class*='drag']",
        "//div[contains(@class,'slider') and contains(@class,'btn')]",
        "//div[contains(@class,'slide')]//div[contains(@class,'handle') or contains(@class,'btn')]",
    ]
    slider = None
    for sel in slider_selectors:
        try:
            if sel.startswith("//"):
                slider = driver.find_element(By.XPATH, sel)
            else:
                slider = driver.find_element(By.CSS_SELECTOR, sel)
            if slider.is_displayed():
                logger.info(f"Tìm thấy slider: {sel}")
                break
            slider = None
        except Exception:
            continue

    if slider is None:
        logger.warning("Không tìm thấy thanh slider")
        return False

    actions = ActionChains(driver)
    # Kéo slider sang phải theo khoảng cách AI chỉ định
    # Dùng kéo từ từ (human-like) để tránh bot detection
    actions.click_and_hold(slider)
    steps = max(distance // 10, 5)
    step_px = distance / steps
    for i in range(steps):
        actions.move_by_offset(int(step_px), 0)
        actions.pause(0.03)
    actions.release()
    actions.perform()
    time.sleep(1.5)
    return True


# ── Nhấn OK sau khi hoàn thành ────────────────────────────────────────────
def _click_ok(driver):
    ok_xpaths = [
        "//*[normalize-space(text())='OK']",
        "//*[normalize-space(text())='ok']",
        "//*[normalize-space(text())='Xác nhận']",
        "//*[contains(@class,'submit') and not(contains(@style,'display:none'))]",
        "//*[contains(@class,'geetest_submit')]",
    ]
    for xpath in ok_xpaths:
        try:
            el = driver.find_element(By.XPATH, xpath)
            if el.is_displayed():
                el.click()
                logger.info(f"Đã nhấn OK: {xpath}")
                time.sleep(1)
                return
        except Exception:
            continue
    logger.info("Không tìm thấy nút OK (có thể tự submit)")


# ── Hàm chính ──────────────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    """
    Phát hiện và giải captcha trên trang hiện tại.
    Hỗ trợ: click-order (bấm theo thứ tự) + slide (lướt hình).
    Trả về True nếu thành công.
    """
    time.sleep(2)   # Chờ captcha widget render xong

    # Chụp toàn trang → không cần tìm container
    try:
        png_bytes = driver.get_screenshot_as_png()
        image_b64 = base64.b64encode(png_bytes).decode()
    except Exception as e:
        logger.error(f"Không chụp được màn hình: {e}")
        return False

    result = _ask_ai(image_b64)
    ctype  = result.get("type", "none")

    if ctype == "click":
        coords = result.get("coords", [])
        logger.info(f"Loại: click-order | Tọa độ: {coords}")
        return _do_click_captcha(driver, coords)

    elif ctype == "slide":
        distance = int(result.get("distance", 100))
        logger.info(f"Loại: slide | Khoảng cách: {distance}px")
        return _do_slide_captcha(driver, distance)

    elif ctype == "error":
        logger.error(f"Lỗi AI: {result.get('msg')}")
        return False

    else:
        logger.info("AI không phát hiện captcha hoặc loại không xác định")
        return False

"""
AI-powered solver cho captcha dạng "Chọn theo thứ tự" trên fly88h.com.
- Chụp ảnh widget captcha
- Gửi AI (GPT-4o hoặc Gemini) để nhận tọa độ cần click theo thứ tự
- Click từng điểm, rồi nhấn OK
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
AI_PROVIDER    = os.environ.get("AI_CAPTCHA_PROVIDER", "openai")  # openai | gemini

CLICK_ORDER_PROMPT = """This is a Vietnamese click-order image captcha ("Chọn theo thứ tự này" = "Select in this order").

At the TOP of the image there is a row of small icon thumbnails — that is the REQUIRED click order.
In the MAIN image below, the same icons appear scattered around at larger size.

Your task:
1. Identify the icons shown in the top row (left to right = click order 1, 2, 3, 4...).
2. Find each matching icon in the main image area.
3. Return ONLY a JSON array of [x, y] pixel coordinates (relative to the top-left of this image) in click order.

Format — return ONLY this, nothing else:
[[x1, y1], [x2, y2], [x3, y3], [x4, y4]]

If you cannot determine the coordinates, return an empty array: []
"""


def _ask_openai(image_b64: str) -> str:
    import urllib.request, urllib.error

    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text",      "text": CLICK_ORDER_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }
        ],
        "max_tokens": 120,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
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
                {"text": CLICK_ORDER_PROMPT},
                {"inlineData": {"mimeType": "image/png", "data": image_b64}},
            ]
        }]
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


def _ask_ai(image_b64: str) -> str:
    if AI_PROVIDER == "gemini" and GOOGLE_API_KEY:
        logger.info("Gửi captcha lên Gemini Vision...")
        return _ask_gemini(image_b64)
    elif OPENAI_API_KEY:
        logger.info("Gửi captcha lên OpenAI GPT-4o Vision...")
        return _ask_openai(image_b64)
    raise RuntimeError("Không có AI API key nào được cấu hình (OPENAI_API_KEY hoặc GOOGLE_API_KEY)")


def _parse_coords(text: str):
    """Trích xuất mảng tọa độ [[x,y],...] từ chuỗi AI trả về."""
    match = re.search(r'\[\s*\[[\d\s,.\[\]]+\]\s*\]', text)
    if not match:
        return None
    try:
        coords = json.loads(match.group())
        if isinstance(coords, list) and all(len(c) == 2 for c in coords):
            return coords
    except Exception:
        pass
    return None


def _find_captcha_container(driver):
    """Tìm container widget captcha trên trang."""
    # Thử tìm theo class thường gặp (GeeTest, v.v.)
    class_selectors = [
        "[class*='geetest_wind']",
        "[class*='geetest_panel']",
        "[class*='geetest_']",
        "[class*='captcha-wrap']",
        "[class*='captcha_wrap']",
        "[class*='captcha-modal']",
        "[class*='captcha']",
    ]
    for sel in class_selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed() and el.size["width"] > 50:
                logger.info(f"Tìm thấy captcha container: {sel}")
                return el
        except Exception:
            continue

    # Thử tìm qua text "Chọn theo thứ tự" rồi leo lên DOM
    try:
        text_el = driver.find_element(
            By.XPATH, "//*[contains(text(),'Chọn theo thứ tự')]"
        )
        # Leo lên vài cấp để lấy toàn bộ widget
        el = text_el
        for _ in range(6):
            parent = el.find_element(By.XPATH, "..")
            w = parent.size.get("width", 0)
            h = parent.size.get("height", 0)
            if w > 200 and h > 200:
                logger.info("Tìm thấy captcha container qua text 'Chọn theo thứ tự'")
                return parent
            el = parent
    except Exception:
        pass

    return None


def solve_captcha_on_page(driver) -> bool:
    """
    Giải captcha "Chọn theo thứ tự" bằng AI Vision.
    Trả về True nếu thành công, False nếu không tìm thấy hoặc lỗi.
    """
    # Chờ thêm để captcha widget load xong
    time.sleep(2)

    container = _find_captcha_container(driver)
    if container is None:
        logger.warning("Không tìm thấy captcha container.")
        return False

    # Chụp ảnh toàn bộ widget captcha
    try:
        png_bytes = container.screenshot_as_png
    except Exception as e:
        # Fallback: chụp toàn trang
        logger.warning(f"Không chụp được container, chụp toàn trang: {e}")
        png_bytes = driver.get_screenshot_as_png()

    image_b64 = base64.b64encode(png_bytes).decode()

    # Gửi AI phân tích
    try:
        answer = _ask_ai(image_b64)
        logger.info(f"AI trả lời: {answer}")
    except Exception as e:
        logger.error(f"Lỗi gọi AI: {e}")
        return False

    coords = _parse_coords(answer)
    if not coords:
        logger.warning(f"Không parse được tọa độ từ: {answer}")
        return False

    logger.info(f"Tọa độ cần click: {coords}")

    # Lấy vị trí container trên trang để quy đổi tọa độ
    loc  = container.location_once_scrolled_into_view
    size = container.size
    cx_offset = size["width"]  // 2
    cy_offset = size["height"] // 2

    actions = ActionChains(driver)
    actions.move_to_element(container)   # di chuyển đến container

    for idx, (x, y) in enumerate(coords):
        # Tọa độ AI là tính từ góc trên-trái container
        # ActionChains dùng offset từ TÂM element
        off_x = int(x) - cx_offset
        off_y = int(y) - cy_offset
        logger.info(f"Click {idx+1}: offset ({off_x}, {off_y})")
        actions.move_to_element_with_offset(container, off_x, off_y)
        actions.click()
        actions.pause(0.6)

    actions.perform()
    time.sleep(1.5)

    # Nhấn nút OK / Xác nhận sau khi click xong
    ok_selectors = [
        "[class*='geetest_submit']",
        "[class*='captcha_submit']",
        "//button[normalize-space()='OK']",
        "//button[normalize-space()='ok']",
        "//div[normalize-space()='OK']",
        "//*[contains(@class,'btn') and normalize-space()='OK']",
    ]
    for sel in ok_selectors:
        try:
            if sel.startswith("//") or sel.startswith("/"):
                ok_el = driver.find_element(By.XPATH, sel)
            else:
                ok_el = driver.find_element(By.CSS_SELECTOR, sel)
            if ok_el.is_displayed():
                ok_el.click()
                logger.info(f"Đã nhấn OK: {sel}")
                time.sleep(1)
                break
        except Exception:
            continue

    # Fallback: tìm bất kỳ nút/div nào có text "OK" trong container
    try:
        ok_el = container.find_element(
            By.XPATH, ".//*[normalize-space(text())='OK' or normalize-space(text())='ok']"
        )
        if ok_el.is_displayed():
            ok_el.click()
            logger.info("Đã nhấn OK (fallback từ container)")
    except Exception:
        pass

    time.sleep(1)
    return True

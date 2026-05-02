"""
AI-powered captcha solver sử dụng OpenAI Vision hoặc Google Gemini.
Tích hợp cùng Selenium để tự động nhận diện và điền captcha.
"""
import os
import base64
import logging
import time
from io import BytesIO
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY  = os.environ.get("GOOGLE_API_KEY", "")
AI_PROVIDER     = os.environ.get("AI_CAPTCHA_PROVIDER", "openai")  # openai | gemini


# ── Gửi ảnh lên OpenAI Vision ──────────────────────────────────────
def _ask_openai(image_b64: str, prompt: str) -> str:
    import urllib.request, urllib.error, json

    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 50,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"].strip()


# ── Gửi ảnh lên Google Gemini ──────────────────────────────────────
def _ask_gemini(image_b64: str, prompt: str) -> str:
    import urllib.request, json

    model = "gemini-2.0-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GOOGLE_API_KEY}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": image_b64,
                        }
                    },
                ]
            }
        ]
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


# ── Hàm chính: chụp ảnh captcha và giải ──────────────────────────
def solve_captcha_on_page(driver) -> bool:
    """
    Tìm captcha trên trang hiện tại, chụp ảnh, gửi AI giải và điền vào ô.
    Trả về True nếu thành công, False nếu không tìm thấy captcha.
    """
    # Danh sách selector thường gặp cho captcha image
    captcha_selectors = [
        "img.captcha",
        "img[src*='captcha']",
        "img[alt*='captcha']",
        "img[alt*='Captcha']",
        ".captcha img",
        ".captcha-image",
        "#captcha img",
        "canvas.captcha",
    ]

    captcha_el = None
    for sel in captcha_selectors:
        try:
            captcha_el = driver.find_element(By.CSS_SELECTOR, sel)
            if captcha_el.is_displayed():
                logger.info(f"Tìm thấy captcha: {sel}")
                break
        except Exception:
            continue

    if captcha_el is None:
        logger.info("Không tìm thấy captcha trên trang.")
        return False

    # Chụp ảnh vùng captcha
    png_bytes = captcha_el.screenshot_as_png
    image_b64 = base64.b64encode(png_bytes).decode()

    prompt = (
        "This is a CAPTCHA image. Please read the text or numbers shown "
        "and respond with ONLY the characters you see, nothing else. "
        "No spaces, no punctuation, no explanation."
    )

    try:
        if AI_PROVIDER == "gemini" and GOOGLE_API_KEY:
            answer = _ask_gemini(image_b64, prompt)
            logger.info(f"Gemini giải captcha: {answer}")
        elif OPENAI_API_KEY:
            answer = _ask_openai(image_b64, prompt)
            logger.info(f"OpenAI giải captcha: {answer}")
        else:
            logger.warning("Không có API key AI nào được cấu hình. Bỏ qua captcha.")
            return False
    except Exception as e:
        logger.error(f"Lỗi gọi AI: {e}")
        return False

    # Tìm ô input captcha và điền
    input_selectors = [
        "input[placeholder*='captcha']",
        "input[placeholder*='Captcha']",
        "input[name*='captcha']",
        "input[id*='captcha']",
        ".captcha input",
        "#captcha input",
    ]
    for sel in input_selectors:
        try:
            inp = driver.find_element(By.CSS_SELECTOR, sel)
            if inp.is_displayed():
                inp.clear()
                inp.send_keys(answer)
                logger.info(f"Đã điền captcha vào: {sel}")
                return True
        except Exception:
            continue

    # Fallback: điền qua JavaScript
    js_fill = f"""
        var inputs = document.querySelectorAll('input');
        for (var inp of inputs) {{
            var p = (inp.placeholder || '').toLowerCase();
            var n = (inp.name || '').toLowerCase();
            var id = (inp.id || '').toLowerCase();
            if (p.includes('captcha') || n.includes('captcha') || id.includes('captcha')) {{
                inp.value = '{answer}';
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return true;
            }}
        }}
        return false;
    """
    filled = driver.execute_script(js_fill)
    if filled:
        logger.info("Đã điền captcha qua JavaScript fallback.")
    return bool(filled)

"""
Captcha solver cho fly88h.com — hỗ trợ:
  1. GeeTest V4       — dùng 2captcha SDK (geetest_v4)
  2. Click-order      — dùng 2captcha SDK (coordinates) hoặc AI vision
  3. Slide/drag       — dùng 2captcha SDK (coordinates) hoặc AI vision

Thứ tự ưu tiên: 2captcha (nếu có TWOCAPTCHA_API_KEY) → OpenAI → Gemini
"""
import os, base64, json, re, time, logging, tempfile
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
AI_PROVIDER    = os.environ.get("AI_CAPTCHA_PROVIDER", "2captcha")
TWOCAPTCHA_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

# captcha_id cố định của fly88h.com (GeeTest V4)
GEETEST_V4_CAPTCHA_ID = os.environ.get("GEETEST_V4_CAPTCHA_ID", "cff289689d0273ca771b5c1ef63dc8db")
PAGE_URL = os.environ.get("BASE_URL", "https://fly88h.com")


# ─────────────────────────────────────────────────────
#  2captcha SDK helpers
# ─────────────────────────────────────────────────────
def _get_solver():
    from twocaptcha import TwoCaptcha
    return TwoCaptcha(TWOCAPTCHA_KEY)


def solve_geetest_v4(page_url: str = None) -> dict | None:
    """
    Giải GeeTest V4 bằng 2captcha SDK.
    Trả về dict chứa lot_number, pass_token, gen_time, captcha_output.
    """
    if not TWOCAPTCHA_KEY:
        logger.warning("Không có TWOCAPTCHA_API_KEY")
        return None

    url = page_url or f"{PAGE_URL}/home/register"
    logger.info(f"Gửi GeeTest V4 lên 2captcha — captcha_id={GEETEST_V4_CAPTCHA_ID}")
    try:
        solver = _get_solver()
        result = solver.geetest_v4(
            captcha_id=GEETEST_V4_CAPTCHA_ID,
            url=url,
        )
        logger.info(f"GeeTest V4 giải xong: {result}")
        return result
    except Exception as e:
        logger.error(f"Lỗi GeeTest V4 (2captcha): {e}")
        return None


def solve_coordinates_via_2captcha(img_b64: str, instructions: str) -> list:
    """
    Giải click captcha qua 2captcha coordinates API.
    Trả về list tọa độ [[x1,y1], [x2,y2], ...].
    """
    if not TWOCAPTCHA_KEY:
        return []

    try:
        solver = _get_solver()
        # Lưu ảnh tạm để truyền vào SDK
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(base64.b64decode(img_b64))
            tmp_path = tmp.name

        logger.info(f"Gửi click captcha lên 2captcha coordinates — hướng dẫn: {instructions[:50]}")
        result = solver.coordinates(
            file=tmp_path,
            textinstructions=instructions,
        )
        logger.info(f"Coordinates giải xong: {result}")

        os.unlink(tmp_path)

        # SDK trả về dict với key 'code' là list [{x, y}, ...]
        raw = result.get("code", result) if isinstance(result, dict) else result
        if isinstance(raw, list):
            pairs = [[int(p["x"]), int(p["y"])] for p in raw if "x" in p and "y" in p]
        else:
            # Chuỗi dạng "x1=123,y1=456|x2=200,y2=300"
            pairs = []
            for part in str(raw).split("|"):
                nums = re.findall(r'\d+', part)
                if len(nums) >= 2:
                    pairs.append([int(nums[0]), int(nums[1])])

        logger.info(f"Tọa độ click: {pairs}")
        return pairs

    except Exception as e:
        logger.error(f"Lỗi coordinates (2captcha): {e}")
        return []


# ─────────────────────────────────────────────────────
#  AI vision prompts (dự phòng khi không có 2captcha)
# ─────────────────────────────────────────────────────
PROMPT_CLICK = """You are an image analysis assistant. Analyze this UI widget image.

IMAGE LAYOUT (top to bottom):
- TOP STRIP (top ~15%): a row of small numbered/icon thumbnails labeled "Chọn theo thứ tự này:" — these define the REQUIRED SELECTION ORDER (left = first, right = last).
- MAIN PHOTO AREA (~15%–70%): a photo background where the SAME symbols/icons appear scattered at larger size.
- BOTTOM AREA: a confirmation button labeled "OK".

YOUR TASK:
1. Identify the icons in the TOP STRIP from left to right (that is the required order).
2. For each icon, locate its CENTER pixel position inside the MAIN PHOTO AREA.
3. List coordinates in the required order.

Return ONLY this JSON (no explanation, no markdown):
{"type":"click","coords":[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}

All coordinates are pixels relative to the TOP-LEFT corner of this image.
"""

PROMPT_SLIDE = """You are an image analysis assistant. Analyze this UI puzzle widget image.

IMAGE LAYOUT:
- A background photo with a DARK RECTANGULAR GAP/NOTCH cut into it.
- A small floating tile/piece that visually matches the gap.
- A horizontal DRAG BAR at the bottom with a circular handle positioned on the left side.

YOUR TASK: Calculate how many pixels the circular handle must move to the RIGHT so the floating tile perfectly fills the gap in the background photo.

Return ONLY this JSON (no explanation, no markdown):
{"type":"slide","distance":<integer_pixels>}
"""

PROMPT_DETECT = """You are a UI analyst. Look at this website screenshot and identify whether a verification widget popup is currently displayed.

Widget types to look for:
- "click"  → popup with "Chọn theo thứ tự này" text, icon thumbnails at top, photo below
- "slide"  → popup with a puzzle piece and a horizontal drag bar ("Lướt hình")
- "geetest"→ GeeTest V4 widget (logo GeeTest, loading spinner, hoặc overlay verify)
- "none"   → no such popup visible on screen

Return ONLY valid JSON:
{"type":"click"} or {"type":"slide"} or {"type":"geetest"} or {"type":"none"}
"""


# ─────────────────────────────────────────────────────
#  AI vision fallback
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


def _is_geetest_v4(driver) -> bool:
    """Kiểm tra có widget GeeTest V4 không."""
    return driver.execute_script("""
        return !!(
            document.querySelector('[class*="geetest_wind"]') ||
            document.querySelector('[class*="geetest_box"]') ||
            document.querySelector('iframe[src*="geetest"]') ||
            (typeof window.initGeetest4 !== 'undefined')
        );
    """)


# ─────────────────────────────────────────────────────
#  Crop modal
# ─────────────────────────────────────────────────────
def _crop_modal(driver, rect: dict) -> str:
    full_png = driver.get_screenshot_as_png()
    img = Image.open(BytesIO(full_png))
    vp_w = driver.execute_script("return window.innerWidth;")
    dpr  = img.width / vp_w if vp_w else 1.0
    x1 = max(0, int(rect["x"] * dpr))
    y1 = max(0, int(rect["y"] * dpr))
    x2 = min(img.width,  int((rect["x"] + rect["w"]) * dpr))
    y2 = min(img.height, int((rect["y"] + rect["h"]) * dpr))
    cropped = img.crop((x1, y1, x2, y2))
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ─────────────────────────────────────────────────────
#  Submit GeeTest V4 solution lên trang
# ─────────────────────────────────────────────────────
def _submit_geetest_v4(driver, solution: dict) -> bool:
    """
    Inject kết quả GeeTest V4 vào trang để tự động xác nhận.
    2captcha trả về: captcha_id, lot_number, pass_token, gen_time, captcha_output
    """
    try:
        # Lấy các trường từ solution (SDK có thể trả nested)
        data = solution if isinstance(solution, dict) else {}
        # SDK trả về dạng {'code': '...', ...} hoặc nested
        code = data.get("code", "")
        if isinstance(code, str) and "lot_number" in code:
            try:
                data = json.loads(code)
            except Exception:
                pass

        lot_number     = data.get("lot_number", data.get("lotNumber", ""))
        pass_token     = data.get("pass_token", data.get("passToken", ""))
        gen_time       = data.get("gen_time", data.get("genTime", ""))
        captcha_output = data.get("captcha_output", data.get("captchaOutput", ""))

        logger.info(f"Submit GeeTest V4: lot={lot_number[:10]}... pass={pass_token[:10]}...")

        # Inject kết quả vào callback của trang
        injected = driver.execute_script(f"""
            try {{
                var result = {{
                    captcha_id: "{GEETEST_V4_CAPTCHA_ID}",
                    lot_number: "{lot_number}",
                    pass_token: "{pass_token}",
                    gen_time: "{gen_time}",
                    captcha_output: "{captcha_output}"
                }};

                // Thử gọi callback nếu trang expose sẵn
                if (typeof window._gt4Callback === 'function') {{
                    window._gt4Callback(result);
                    return 'callback_ok';
                }}

                // Thử điền hidden inputs nếu có
                var fields = ['lot_number','pass_token','gen_time','captcha_output'];
                var filled = 0;
                fields.forEach(function(f) {{
                    var el = document.querySelector('input[name="' + f + '"]');
                    if (el) {{ el.value = result[f]; filled++; }}
                }});
                if (filled > 0) return 'inputs_filled:' + filled;

                // Lưu vào window để automation có thể dùng sau
                window.__geetest4_result = result;
                return 'stored';
            }} catch(e) {{
                return 'error:' + e.message;
            }}
        """)
        logger.info(f"GeeTest V4 inject result: {injected}")
        time.sleep(1)
        return True

    except Exception as e:
        logger.error(f"Lỗi submit GeeTest V4: {e}")
        return False


# ─────────────────────────────────────────────────────
#  Click từng icon theo thứ tự
# ─────────────────────────────────────────────────────
def _do_click(driver, coords: list, modal_rect: dict) -> bool:
    if not coords:
        logger.warning("Không có tọa độ click")
        return False

    vp_w = driver.execute_script("return window.innerWidth;")
    full = driver.get_screenshot_as_png()
    dpr  = Image.open(BytesIO(full)).width / vp_w if vp_w else 1.0

    for idx, (cx, cy) in enumerate(coords):
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
    time.sleep(2)

    # Bước 1: Kiểm tra GeeTest V4 trước (ưu tiên cao nhất)
    if TWOCAPTCHA_KEY and _is_geetest_v4(driver):
        logger.info("Phát hiện GeeTest V4 — giải qua 2captcha SDK...")
        current_url = driver.current_url
        solution = solve_geetest_v4(page_url=current_url)
        if solution:
            return _submit_geetest_v4(driver, solution)
        logger.warning("GeeTest V4 giải thất bại, thử phương án khác...")

    # Bước 2: Tìm modal captcha click/slide
    modal = _get_modal_rect(driver)

    if modal:
        img_b64 = _crop_modal(driver, modal)
        logger.info("Đã crop ảnh modal captcha")
    else:
        logger.warning("Không tìm thấy modal → dùng full page")
        img_b64 = base64.b64encode(driver.get_screenshot_as_png()).decode()
        modal   = {"x": 0, "y": 0, "w": 1280, "h": 800}

    # Bước 3: Phát hiện loại captcha
    det   = _ai(img_b64, PROMPT_DETECT)
    ctype = det.get("type", "none")
    logger.info(f"Loại captcha phát hiện: {ctype}")

    if ctype == "none":
        return False

    if ctype == "geetest" and TWOCAPTCHA_KEY:
        current_url = driver.current_url
        solution = solve_geetest_v4(page_url=current_url)
        if solution:
            return _submit_geetest_v4(driver, solution)
        return False

    if ctype == "click":
        if TWOCAPTCHA_KEY:
            logger.info("Dùng 2captcha coordinates để giải click-order...")
            coords = solve_coordinates_via_2captcha(
                img_b64,
                "Nhấp vào các biểu tượng theo đúng thứ tự được chỉ định từ trái sang phải"
            )
        else:
            res    = _ai(img_b64, PROMPT_CLICK)
            coords = res.get("coords", [])
        logger.info(f"Tọa độ click: {coords}")
        return _do_click(driver, coords, modal)

    if ctype == "slide":
        if TWOCAPTCHA_KEY:
            logger.info("Dùng 2captcha coordinates để giải slide...")
            pts = solve_coordinates_via_2captcha(
                img_b64,
                "Kéo mảnh ghép vào đúng vị trí trong ảnh nền"
            )
            dist = int(pts[0][0]) if pts else 120
        else:
            res  = _ai(img_b64, PROMPT_SLIDE)
            dist = int(res.get("distance", 120))
        logger.info(f"Slide distance: {dist}px")
        return _do_slide(driver, dist)

    return False

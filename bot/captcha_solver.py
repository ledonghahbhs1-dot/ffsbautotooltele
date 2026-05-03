"""
Captcha solver cho fly88h.com — chỉ dùng 2captcha SDK.
  1. GeeTest V4    — solver.geetest_v4(captcha_id, url)
  2. Click captcha — solver.coordinates(file, textinstructions)
"""
import os, base64, json, re, time, logging, tempfile
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from twocaptcha import TwoCaptcha

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("TWOCAPTCHA_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://fly88h.com")

GEETEST_V4_ID  = os.getenv("GEETEST_V4_CAPTCHA_ID", "cff289689d0273ca771b5c1ef63dc8db")
REGISTER_URL   = f"{BASE_URL}/home/register"

if not API_KEY:
    logger.error("Không tìm thấy TWOCAPTCHA_API_KEY ❌")
    solver = None
else:
    solver = TwoCaptcha(API_KEY)


# ─────────────────────────────────────────────────────
# 1. GIẢI GEETEST V4
# ─────────────────────────────────────────────────────
def giai_geetest_v4(website_url: str = None) -> dict | None:
    if not solver:
        return None
    url = website_url or REGISTER_URL
    logger.info(f"Đang nhờ 2Captcha giải GeeTest V4... captcha_id={GEETEST_V4_ID}")
    try:
        result = solver.geetest_v4(
            captcha_id=GEETEST_V4_ID,
            url=url,
        )
        logger.info(f"Giải V4 thành công ✅: pass_token={str(result.get('pass_token',''))[:12]}...")
        return result
    except Exception as e:
        logger.error(f"Lỗi giải GeeTest V4: {e} ❌")
        return None


# ─────────────────────────────────────────────────────
# 2. GIẢI CLICK CAPTCHA (tọa độ ảnh)
# ─────────────────────────────────────────────────────
def giai_click_captcha(image_path: str, huong_dan: str) -> list:
    if not solver:
        return []
    logger.info(f"Đang gửi ảnh lấy tọa độ Click Captcha... 🖼️")
    try:
        result = solver.coordinates(
            file=image_path,
            textinstructions=huong_dan,
        )
        logger.info(f"Tọa độ click nhận được: {result}")

        raw = result.get("code", result) if isinstance(result, dict) else result
        if isinstance(raw, list):
            pairs = [[int(p["x"]), int(p["y"])] for p in raw if "x" in p and "y" in p]
        else:
            pairs = []
            for part in str(raw).split("|"):
                nums = re.findall(r'\d+', part)
                if len(nums) >= 2:
                    pairs.append([int(nums[0]), int(nums[1])])

        logger.info(f"✅ Tọa độ đã xử lý: {pairs}")
        return pairs
    except Exception as e:
        logger.error(f"Lỗi giải Click Captcha: {e} ❌")
        return []


# ─────────────────────────────────────────────────────
# INJECT KẾT QUẢ GEETEST V4 VÀO TRANG
# ─────────────────────────────────────────────────────
def _submit_geetest_v4(driver, result: dict) -> bool:
    try:
        lot_number     = result.get("lot_number", "")
        pass_token     = result.get("pass_token", "")
        gen_time       = result.get("gen_time", "")
        captcha_output = result.get("captcha_output", "")

        logger.info(f"Inject GeeTest V4: lot={lot_number[:10]}...")

        status = driver.execute_script(f"""
            try {{
                var res = {{
                    captcha_id: "{GEETEST_V4_ID}",
                    lot_number: "{lot_number}",
                    pass_token: "{pass_token}",
                    gen_time: "{gen_time}",
                    captcha_output: "{captcha_output}"
                }};
                if (typeof window._gt4Callback === 'function') {{
                    window._gt4Callback(res); return 'callback_ok';
                }}
                var filled = 0;
                ['lot_number','pass_token','gen_time','captcha_output'].forEach(function(f) {{
                    var el = document.querySelector('input[name="' + f + '"]');
                    if (el) {{ el.value = res[f]; filled++; }}
                }});
                if (filled > 0) return 'inputs_filled:' + filled;
                window.__geetest4_result = res;
                return 'stored';
            }} catch(e) {{ return 'error:' + e.message; }}
        """)
        logger.info(f"Inject result: {status}")
        time.sleep(1)
        return True
    except Exception as e:
        logger.error(f"Lỗi inject GeeTest V4: {e}")
        return False


# ─────────────────────────────────────────────────────
# TÌM VÀ CROP MODAL CAPTCHA
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
        let tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while (node = tw.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes('Chọn theo thứ tự')) {
                let r = getRect(node.parentElement, 200, 200);
                if (r) return {x: r.left, y: r.top, w: r.width, h: r.height};
            }
        }
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


def _crop_modal_to_file(driver, rect: dict) -> str | None:
    """Crop ảnh modal → lưu file tạm → trả về đường dẫn."""
    try:
        full_png = driver.get_screenshot_as_png()
        img = Image.open(BytesIO(full_png))
        vp_w = driver.execute_script("return window.innerWidth;")
        dpr  = img.width / vp_w if vp_w else 1.0
        x1 = max(0, int(rect["x"] * dpr))
        y1 = max(0, int(rect["y"] * dpr))
        x2 = min(img.width,  int((rect["x"] + rect["w"]) * dpr))
        y2 = min(img.height, int((rect["y"] + rect["h"]) * dpr))
        cropped = img.crop((x1, y1, x2, y2))
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        cropped.save(tmp.name, format="PNG")
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.error(f"Lỗi crop modal: {e}")
        return None


# ─────────────────────────────────────────────────────
# THỰC HIỆN CLICK VÀ NHẤN OK
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


def _press_ok(driver, modal_rect: dict = None):
    for xpath in [
        "//*[normalize-space(text())='OK']",
        "//*[normalize-space(text())='ok']",
        "//*[contains(@class,'geetest_submit')]",
        "//*[contains(@class,'submit')]",
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

    if modal_rect:
        px = int(modal_rect["x"] + modal_rect["w"] * 0.49)
        py = int(modal_rect["y"] + modal_rect["h"] * 0.74)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e)e.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));",
            px, py,
        )
        time.sleep(1)


# ─────────────────────────────────────────────────────
# HÀM CHÍNH — GỌI TỪ automation.py
# ─────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    if not solver:
        logger.error("solver=None, bỏ qua captcha ❌")
        return False

    time.sleep(2)

    # Bước 1: Thử GeeTest V4 (fly88h.com dùng loại này khi đăng ký)
    current_url = driver.current_url
    logger.info("Thử giải GeeTest V4 qua 2captcha...")
    result_v4 = giai_geetest_v4(website_url=current_url)

    if result_v4:
        ok = _submit_geetest_v4(driver, result_v4)
        if ok:
            time.sleep(1)
            # Click lại ĐĂNG KÝ để submit form sau khi có token
            driver.execute_script("""
                let all = Array.from(document.querySelectorAll('div, button, span'))
                    .filter(e => e.innerText && e.innerText.trim() === 'ĐĂNG KÝ' && e.offsetParent !== null);
                if (all.length > 0) {
                    let btn = all[all.length - 1];
                    const opt = { bubbles: true, cancelable: true, view: window };
                    btn.dispatchEvent(new MouseEvent('mousedown', opt));
                    btn.dispatchEvent(new MouseEvent('mouseup', opt));
                    btn.click();
                }
            """)
            logger.info("✅ GeeTest V4 xong, đã click ĐĂNG KÝ")
            return True

    # Bước 2: Tìm modal click captcha (nếu GeeTest V4 không có)
    logger.info("GeeTest V4 thất bại, tìm modal click captcha...")
    modal = _get_modal_rect(driver)
    if not modal:
        logger.warning("Không tìm thấy captcha nào ❌")
        return False

    img_path = _crop_modal_to_file(driver, modal)
    if not img_path:
        return False

    try:
        coords = giai_click_captcha(
            image_path=img_path,
            huong_dan="Nhấp vào các biểu tượng theo đúng thứ tự được chỉ định từ trái sang phải",
        )
        if coords:
            return _do_click(driver, coords, modal)

        logger.warning("Không lấy được tọa độ click ❌")
        return False
    finally:
        try:
            os.unlink(img_path)
        except Exception:
            pass

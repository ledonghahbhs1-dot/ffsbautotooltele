"""
Captcha solver cho fly88h.com — chỉ dùng 2captcha SDK.

Luồng chính:
  1. Tìm modal click captcha (Chọn theo thứ tự này)
  2. Crop ảnh → gửi solver.coordinates() → nhận tọa độ
  3. Click từng điểm → nhấn OK
  4. Nếu không tìm thấy click captcha → thử GeeTest V4 token
"""
import os, base64, json, re, time, random, logging, tempfile
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from twocaptcha import TwoCaptcha

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("TWOCAPTCHA_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://fly88h.com")
GEETEST_V4_ID = os.getenv("GEETEST_V4_CAPTCHA_ID", "cff289689d0273ca771b5c1ef63dc8db")
REGISTER_URL  = f"{BASE_URL}/home/register"

if not API_KEY:
    logger.error("Không tìm thấy TWOCAPTCHA_API_KEY ❌")
    solver = None
else:
    solver = TwoCaptcha(API_KEY)


# ─────────────────────────────────────────────────────
# 1. GIẢI CLICK CAPTCHA — gửi ảnh lên 2captcha
# ─────────────────────────────────────────────────────
def giai_click_captcha(image_path: str, huong_dan: str) -> list:
    """Gửi ảnh → 2captcha trả tọa độ [[x,y], ...]"""
    if not solver:
        return []
    logger.info(f"Gửi ảnh Click Captcha lên 2captcha... 🖼️")
    try:
        result = solver.coordinates(
            file=image_path,
            textinstructions=huong_dan,
        )
        logger.info(f"✅ Tọa độ nhận được: {result}")

        raw = result.get("code", result) if isinstance(result, dict) else result

        pairs = []
        if isinstance(raw, list):
            # Dạng list của dict: [{"x":112,"y":109}, ...]
            for p in raw:
                if isinstance(p, dict) and "x" in p and "y" in p:
                    pairs.append([int(p["x"]), int(p["y"])])
        else:
            # Dạng string: "coordinates:x=112,y=109;x=40,y=88;..."
            raw_str = str(raw)
            raw_str = re.sub(r'^coordinates:', '', raw_str, flags=re.IGNORECASE).strip()
            # Tách từng điểm bằng ";" (định dạng 2captcha trả về)
            for part in raw_str.split(";"):
                part = part.strip()
                if not part:
                    continue
                x_m = re.search(r'x=(\d+)', part, re.IGNORECASE)
                y_m = re.search(r'y=(\d+)', part, re.IGNORECASE)
                if x_m and y_m:
                    pairs.append([int(x_m.group(1)), int(y_m.group(1))])
                else:
                    # Fallback: lấy 2 số đầu tiên trong phần
                    nums = re.findall(r'\d+', part)
                    if len(nums) >= 2:
                        pairs.append([int(nums[0]), int(nums[1])])

        logger.info(f"Tọa độ đã xử lý ({len(pairs)} điểm): {pairs}")
        return pairs
    except Exception as e:
        logger.error(f"Lỗi giải Click Captcha: {e} ❌")
        return []


# ─────────────────────────────────────────────────────
# 2. GIẢI GEETEST V4 (dự phòng)
# ─────────────────────────────────────────────────────
def giai_geetest_v4(website_url: str = None) -> dict | None:
    if not solver:
        return None
    url = website_url or REGISTER_URL
    logger.info(f"Thử GeeTest V4 qua 2captcha... captcha_id={GEETEST_V4_ID}")
    try:
        result = solver.geetest_v4(captcha_id=GEETEST_V4_ID, url=url)
        logger.info(f"GeeTest V4 ✅: lot={str(result.get('lot_number',''))[:10]}...")
        return result
    except Exception as e:
        logger.error(f"Lỗi GeeTest V4: {e} ❌")
        return None


def _submit_geetest_v4(driver, result: dict) -> bool:
    try:
        lot_number     = result.get("lot_number", "")
        pass_token     = result.get("pass_token", "")
        gen_time       = result.get("gen_time", "")
        captcha_output = result.get("captcha_output", "")
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
        logger.info(f"GeeTest V4 inject: {status}")
        time.sleep(1)
        return True
    except Exception as e:
        logger.error(f"Lỗi inject GeeTest V4: {e}")
        return False


# ─────────────────────────────────────────────────────
# TÌM MODAL CAPTCHA
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
# CLICK CÁC TỌA ĐỘ
# ─────────────────────────────────────────────────────
def _human_sleep(min_s: float, max_s: float):
    """Nghỉ ngẫu nhiên giống người thật."""
    time.sleep(random.uniform(min_s, max_s))


def _do_click(driver, coords: list, modal_rect: dict) -> bool:
    if not coords:
        logger.warning("Không có tọa độ click")
        return False

    vp_w = driver.execute_script("return window.innerWidth;")
    full = driver.get_screenshot_as_png()
    dpr  = Image.open(BytesIO(full)).width / vp_w if vp_w else 1.0

    # Nghỉ nhỏ trước khi bắt đầu click (giống người nhìn ảnh trước)
    _human_sleep(0.4, 0.9)

    for idx, (cx, cy) in enumerate(coords):
        # Jitter nhỏ ±3px để không click đúng pixel — giống người thật
        jitter_x = random.randint(-3, 3)
        jitter_y = random.randint(-3, 3)
        px = int(modal_rect["x"] + cx / dpr) + jitter_x
        py = int(modal_rect["y"] + cy / dpr) + jitter_y
        logger.info(f"  Click {idx+1}: crop({cx},{cy}) → viewport({px},{py})")

        # mousedown giữ 80-180ms rồi mới mouseup (giống tay người)
        hold_ms = random.randint(80, 180)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "  e.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));"
            "  e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}",
            px, py,
        )
        time.sleep(hold_ms / 1000)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "  e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "  e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}",
            px, py,
        )

        # Nghỉ giữa các click: 0.7–1.6 giây (giống người suy nghĩ)
        _human_sleep(0.7, 1.6)

    # Nghỉ thêm trước khi nhấn OK (giống người kiểm tra lại)
    _human_sleep(0.5, 1.2)
    return True


# ─────────────────────────────────────────────────────
# NHẤN NÚT OK — nhiều cách thử
# ─────────────────────────────────────────────────────
def _press_ok(driver, modal_rect: dict = None) -> bool:
    # Cách 1: Tìm qua JS text content (đáng tin nhất)
    clicked = driver.execute_script("""
        var candidates = Array.from(document.querySelectorAll('div, button, span, a'))
            .filter(function(el) {
                var txt = (el.innerText || el.textContent || '').trim();
                return (txt === 'OK' || txt === 'ok' || txt === 'Xác nhận' || txt === 'Confirm')
                    && el.offsetParent !== null;
            });
        if (candidates.length > 0) {
            var btn = candidates[candidates.length - 1];
            var r = btn.getBoundingClientRect();
            btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
            btn.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true}));
            btn.click();
            return 'js_text:' + btn.tagName + '/' + (btn.className||'').substring(0,30);
        }
        return null;
    """)
    if clicked:
        logger.info(f"OK nhấn thành công qua JS: {clicked}")
        time.sleep(1.5)
        return True

    # Cách 2: XPath
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
                logger.info(f"OK nhấn qua XPath: {xpath}")
                time.sleep(1.5)
                return True
        except Exception:
            continue

    # Cách 3: Click vị trí cố định trong modal (74% chiều cao = vị trí nút OK)
    if modal_rect:
        for ratio_y in [0.74, 0.78, 0.70]:
            px = int(modal_rect["x"] + modal_rect["w"] * 0.50)
            py = int(modal_rect["y"] + modal_rect["h"] * ratio_y)
            logger.info(f"OK fallback click tại ({px},{py}) ratio_y={ratio_y}")
            driver.execute_script(
                "var e=document.elementFromPoint(arguments[0],arguments[1]);"
                "if(e){"
                "  e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "  e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));"
                "  e.click();"
                "}",
                px, py,
            )
            time.sleep(1.5)
            # Kiểm tra modal còn không
            still_visible = driver.execute_script("""
                var tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                var node;
                while (node = tw.nextNode()) {
                    if (node.nodeValue && node.nodeValue.includes('Chọn theo thứ tự')) return true;
                }
                return false;
            """)
            if not still_visible:
                logger.info("Modal đã biến mất sau OK click ✅")
                return True

    logger.warning("Không tìm thấy nút OK ❌")
    return False


# ─────────────────────────────────────────────────────
# GRID CAPTCHA (dạng lưới ô vuông)
# ─────────────────────────────────────────────────────
def _get_grid_rect(driver) -> dict | None:
    """Tìm captcha dạng lưới ô hình ảnh."""
    rect = driver.execute_script(r"""
        var selectors = [
            '[class*="geetest_item"]', '[class*="captcha_item"]',
            '[class*="grid"]', '[class*="task-image"]',
            '[class*="tile"]', '[class*="cell"]'
        ];
        for (var s of selectors) {
            var els = document.querySelectorAll(s);
            if (els.length >= 4) {
                var parent = els[0].parentElement;
                for (var i = 0; i < 5; i++) {
                    if (!parent) break;
                    var r = parent.getBoundingClientRect();
                    if (r.width > 100 && r.height > 100 && r.top > 0)
                        return {x: r.left, y: r.top, w: r.width, h: r.height, count: els.length, selector: s};
                    parent = parent.parentElement;
                }
            }
        }
        return null;
    """)
    if rect:
        logger.info(f"Grid rect: {rect}")
    return rect


def giai_grid_captcha(image_path: str, rows: int = 3, cols: int = 3, hint: str = "") -> list:
    """Gửi ảnh grid → 2captcha trả số thứ tự ô cần click (1-indexed)."""
    if not solver:
        return []
    logger.info(f"Gửi Grid Captcha lên 2captcha ({rows}x{cols})...")
    try:
        result = solver.grid(
            image_path,
            hintText=hint or "Chọn tất cả các ô phù hợp với yêu cầu",
            rows=rows,
            cols=cols,
        )
        logger.info(f"Grid result: {result}")
        code = result.get("code", "") if isinstance(result, dict) else str(result)
        code = re.sub(r'^click:', '', code, flags=re.IGNORECASE).strip()
        tiles = [int(x) for x in code.split(",") if x.strip().isdigit()]
        logger.info(f"Ô cần click: {tiles}")
        return tiles
    except Exception as e:
        logger.error(f"Lỗi Grid captcha: {e} ❌")
        return []


def _click_grid_tiles(driver, tiles: list, selector: str):
    """Click vào các ô theo index (1-based) trong lưới grid."""
    els = driver.find_elements("css selector", selector)
    if not els:
        # Fallback selector list
        for fb in ['[class*="geetest_item"]', '[class*="task-image"]',
                   '[class*="captcha_item"]', '[class*="tile"]']:
            els = driver.find_elements("css selector", fb)
            if els:
                break
    if not els:
        logger.warning("Không tìm thấy ô grid để click ❌")
        return

    logger.info(f"Grid: {len(els)} ô tìm thấy, cần click: {tiles}")
    for tile_idx in tiles:
        arr_idx = tile_idx - 1
        if 0 <= arr_idx < len(els):
            try:
                _human_sleep(0.5, 1.1)
                els[arr_idx].click()
                logger.info(f"  Grid click ô {tile_idx}")
            except Exception as e:
                logger.warning(f"  Grid click ô {tile_idx} lỗi: {e}")


# ─────────────────────────────────────────────────────
# PHÁT HIỆN LOẠI CAPTCHA HIỆN TẠI
# ─────────────────────────────────────────────────────
def _detect_captcha_type(driver) -> str:
    """
    Trả về: 'click_order' | 'grid' | 'none'
    """
    ctype = driver.execute_script(r"""
        // Click-order: có text "Chọn theo thứ tự"
        var tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        var node;
        while (node = tw.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes('Chọn theo thứ tự'))
                return 'click_order';
        }
        // Grid: có nhiều ô hình ảnh dạng lưới
        var gridSels = ['[class*="geetest_item"]','[class*="captcha_item"]',
                        '[class*="task-image"]','[class*="tile"]','[class*="cell"]'];
        for (var s of gridSels) {
            if (document.querySelectorAll(s).length >= 4) return 'grid';
        }
        return 'none';
    """)
    logger.info(f"Loại captcha hiện tại: {ctype}")
    return ctype or "none"


# ─────────────────────────────────────────────────────
# HÀM CHÍNH — LOOP GIẢI NHIỀU CAPTCHA LIÊN TIẾP
# ─────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    if not solver:
        logger.error("solver=None ❌")
        return False

    MAX_ROUNDS = 5   # Tối đa 5 vòng (phòng vô hạn)
    solved_any = False

    for rnd in range(1, MAX_ROUNDS + 1):
        _human_sleep(1.5, 2.5)
        ctype = _detect_captcha_type(driver)

        # ── Không còn captcha nào ──────────────────────
        if ctype == "none":
            if solved_any:
                logger.info(f"✅ Đã giải xong tất cả captcha ({rnd-1} vòng)")
            else:
                logger.info("Không tìm thấy captcha nào")
            break

        logger.info(f"── Vòng {rnd}: giải captcha loại '{ctype}' ──")

        # ── CLICK-ORDER captcha (Chọn theo thứ tự này) ─
        if ctype == "click_order":
            modal = _get_modal_rect(driver)
            if not modal:
                logger.warning("click_order nhưng không crop được modal")
                break
            img_path = _crop_modal_to_file(driver, modal)
            if not img_path:
                break
            try:
                coords = giai_click_captcha(
                    image_path=img_path,
                    huong_dan="Nhấp vào các biểu tượng theo đúng thứ tự được chỉ định từ trái sang phải ở thanh trên cùng",
                )
            finally:
                try:
                    os.unlink(img_path)
                except Exception:
                    pass

            if not coords:
                logger.warning("Không lấy được tọa độ ❌")
                break

            _do_click(driver, coords, modal)
            ok_pressed = _press_ok(driver, modal)
            logger.info("✅ Click-order hoàn tất" if ok_pressed else "⚠️ Không nhấn được OK")
            solved_any = True

        # ── GRID captcha (lưới ô hình ảnh) ─────────────
        elif ctype == "grid":
            grid_info = _get_grid_rect(driver)
            selector = grid_info.get("selector", '[class*="geetest_item"]') if grid_info else '[class*="geetest_item"]'

            # Crop toàn bộ grid để gửi lên 2captcha
            modal = grid_info or _get_modal_rect(driver)
            if not modal:
                logger.warning("Grid nhưng không tìm thấy vùng ảnh")
                break
            img_path = _crop_modal_to_file(driver, modal)
            if not img_path:
                break
            try:
                # Thử đoán số hàng/cột từ số ô
                count = grid_info.get("count", 9) if grid_info else 9
                if count <= 4:
                    rows, cols = 2, 2
                elif count <= 6:
                    rows, cols = 2, 3
                else:
                    rows, cols = 3, 3

                tiles = giai_grid_captcha(
                    image_path=img_path,
                    rows=rows, cols=cols,
                    hint="Chọn tất cả các ô phù hợp với yêu cầu",
                )
            finally:
                try:
                    os.unlink(img_path)
                except Exception:
                    pass

            if not tiles:
                logger.warning("Không lấy được ô grid ❌")
                break

            _click_grid_tiles(driver, tiles, selector)
            _human_sleep(0.5, 1.0)
            ok_pressed = _press_ok(driver, modal)
            logger.info("✅ Grid hoàn tất" if ok_pressed else "⚠️ Không nhấn được OK")
            solved_any = True

    else:
        # Đã đủ MAX_ROUNDS mà vẫn còn captcha
        logger.warning(f"⚠️ Đã thử {MAX_ROUNDS} vòng, captcha vẫn còn")

    # Nếu không còn captcha hiển thị → dùng GeeTest V4 dự phòng nếu cần
    if not solved_any:
        logger.info("Không tìm thấy click/grid captcha → thử GeeTest V4 dự phòng...")
        current_url = driver.current_url
        result_v4 = giai_geetest_v4(website_url=current_url)
        if result_v4:
            ok = _submit_geetest_v4(driver, result_v4)
            if ok:
                time.sleep(1)
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
                logger.info("✅ GeeTest V4 dự phòng + ĐĂNG KÝ clicked")
                return True
        logger.warning("Không tìm thấy captcha nào ❌")
        return False

    return True

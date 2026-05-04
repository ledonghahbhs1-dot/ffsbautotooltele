"""
Captcha solver cho fly88h.com — chỉ dùng 2captcha SDK.

Luồng:
  1. Chờ captcha container xuất hiện trên trang (GeeTest / modal bất kỳ)
  2. Chụp ảnh vùng captcha
  3. Gửi ảnh lên 2captcha solver.coordinates() — worker người thật click đúng loại
  4. Apply tọa độ → nhấn OK → kiểm tra captcha mới → lặp tối đa 5 vòng
  5. Sau khi hết captcha → click ĐĂNG KÝ submit form
"""
import os, re, time, random, logging, tempfile
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from twocaptcha import TwoCaptcha

logger = logging.getLogger(__name__)

API_KEY  = os.getenv("TWOCAPTCHA_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://fly88h.com")
GEETEST_V4_ID = os.getenv("GEETEST_V4_CAPTCHA_ID", "cff289689d0273ca771b5c1ef63dc8db")
REGISTER_URL  = f"{BASE_URL}/home/register"

solver = TwoCaptcha(API_KEY) if API_KEY else None
if not API_KEY:
    logger.error("Không tìm thấy TWOCAPTCHA_API_KEY ❌")


# ─────────────────────────────────────────────────────
# TIỆN ÍCH
# ─────────────────────────────────────────────────────
def _human_sleep(min_s: float, max_s: float):
    time.sleep(random.uniform(min_s, max_s))


# ─────────────────────────────────────────────────────
# TÌM CAPTCHA CONTAINER — CHỜ TỐI ĐA timeout GIÂY
# ─────────────────────────────────────────────────────
# Selector theo thứ tự ưu tiên — thử từng cái cho đến khi tìm thấy
_CAPTCHA_SELECTORS = [
    '[class*="geetest4_wind"]',
    '[class*="geetest_wind"]',
    '[class*="geetest4_holder"]',
    '[class*="geetest_holder"]',
    '[class*="geetest4_box"]',
    '[class*="geetest"]',
    '[id*="geetest"]',
    '[class*="captcha-modal"]',
    '[class*="captcha_modal"]',
    '[class*="captcha-container"]',
    '[class*="verify-wrap"]',
]


def _find_captcha_rect(driver, timeout: int = 8) -> dict | None:
    """
    Chờ captcha xuất hiện rồi trả về bounding rect của nó.
    Log đầy đủ selector tìm thấy và kích thước để dễ debug.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = driver.execute_script("""
            var selectors = arguments[0];
            for (var i = 0; i < selectors.length; i++) {
                var s = selectors[i];
                var els = document.querySelectorAll(s);
                for (var j = 0; j < els.length; j++) {
                    var el = els[j];
                    var r = el.getBoundingClientRect();
                    if (r.width > 80 && r.height > 80 && r.top >= 0 && r.top < window.innerHeight) {
                        return {
                            x: r.left, y: r.top, w: r.width, h: r.height,
                            selector: s, tag: el.tagName,
                            cls: (el.className || '').toString().substring(0, 60)
                        };
                    }
                }
            }
            return null;
        """, _CAPTCHA_SELECTORS)

        if info:
            logger.info(
                f"[captcha] Tìm thấy: selector='{info['selector']}' "
                f"cls='{info['cls']}' size={info['w']:.0f}x{info['h']:.0f} "
                f"pos=({info['x']:.0f},{info['y']:.0f})"
            )
            return info

        time.sleep(0.6)

    logger.info("[captcha] Không tìm thấy captcha container sau %ds", timeout)
    return None


# ─────────────────────────────────────────────────────
# CHỤP ẢNH CAPTCHA → FILE TẠM
# ─────────────────────────────────────────────────────
def _screenshot_rect_to_file(driver, rect: dict) -> str | None:
    try:
        full_png = driver.get_screenshot_as_png()
        img  = Image.open(BytesIO(full_png))
        vp_w = driver.execute_script("return window.innerWidth;") or 1
        dpr  = img.width / vp_w

        pad = 8   # padding nhỏ xung quanh để worker thấy rõ hơn
        x1 = max(0, int((rect["x"] - pad) * dpr))
        y1 = max(0, int((rect["y"] - pad) * dpr))
        x2 = min(img.width,  int((rect["x"] + rect["w"] + pad) * dpr))
        y2 = min(img.height, int((rect["y"] + rect["h"] + pad) * dpr))

        cropped = img.crop((x1, y1, x2, y2))
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        cropped.save(tmp.name, "PNG")
        tmp.close()
        logger.info(f"[captcha] Ảnh crop: {x2-x1}x{y2-y1}px → {tmp.name}")
        return tmp.name
    except Exception as e:
        logger.error(f"[captcha] Lỗi crop ảnh: {e}")
        return None


# ─────────────────────────────────────────────────────
# PHÁT HIỆN LOẠI CAPTCHA TỪ ẢNH CROP (đơn giản)
# ─────────────────────────────────────────────────────
def _detect_type_from_dom(driver, rect: dict) -> str:
    """
    Phân tích DOM bên trong vùng captcha để xác định loại.
    Trả về: 'click_order' | 'grid' | 'unknown'
    """
    info = driver.execute_script("""
        var rx = arguments[0], ry = arguments[1];
        // Tìm element tại điểm giữa captcha
        var el = document.elementFromPoint(rx + arguments[2]/2, ry + arguments[3]/2);
        if (!el) return {type: 'unknown', html: ''};

        // Leo lên tìm captcha root
        var root = el;
        for (var i = 0; i < 15; i++) {
            if (!root.parentElement) break;
            root = root.parentElement;
            var cls = (root.className || '').toString().toLowerCase();
            if (cls.includes('geetest') || cls.includes('captcha')) break;
        }

        var html = root.innerHTML || '';
        var htmlL = html.toLowerCase();

        // Loại GRID: nhiều ô hình ảnh dạng lưới
        var gridSels = ['[class*="item"]', '[class*="tile"]', '[class*="cell"]',
                        'img', 'canvas'];
        var maxCount = 0;
        gridSels.forEach(function(s) {
            var n = root.querySelectorAll(s).length;
            if (n > maxCount) maxCount = n;
        });
        if (maxCount >= 4) return {type: 'grid', count: maxCount, cls: root.className};

        // Loại CLICK-ORDER: có text chỉ dẫn chọn theo thứ tự
        if (htmlL.includes('ch\\u1ecdn theo th\\u1ee9 t\\u1ef1') ||
            htmlL.includes('click') || htmlL.includes('select') ||
            htmlL.includes('order')) {
            return {type: 'click_order', cls: root.className};
        }

        return {type: 'unknown', cls: root.className.toString().substring(0, 80)};
    """, rect["x"], rect["y"], rect["w"], rect["h"])

    ctype = info.get("type", "unknown") if info else "unknown"
    logger.info(f"[captcha] Loại detect từ DOM: {ctype} | info={info}")
    return ctype


# ─────────────────────────────────────────────────────
# GỬI ẢNH LÊN 2CAPTCHA — COORDINATES (click-order)
# ─────────────────────────────────────────────────────
def _solve_as_coordinates(img_path: str) -> list:
    """Gửi ảnh → 2captcha worker click đúng vị trí → trả tọa độ [[x,y],...]"""
    if not solver:
        return []
    hint = (
        "Hãy nhấp vào các biểu tượng/hình ảnh theo đúng thứ tự số được hiển thị "
        "ở hàng trên cùng (từ trái sang phải). "
        "Nếu là lưới ô hình ảnh, nhấp vào TẤT CẢ ô phù hợp với yêu cầu."
    )
    logger.info("[captcha] Gửi lên 2captcha (coordinates)...")
    try:
        result = solver.coordinates(file=img_path, textinstructions=hint)
        logger.info(f"[captcha] Kết quả thô: {result}")

        raw = result.get("code", result) if isinstance(result, dict) else result
        pairs = []

        if isinstance(raw, list):
            for p in raw:
                if isinstance(p, dict) and "x" in p and "y" in p:
                    pairs.append([int(p["x"]), int(p["y"])])
        else:
            raw_str = re.sub(r'^coordinates:', '', str(raw), flags=re.IGNORECASE).strip()
            for part in raw_str.split(";"):
                part = part.strip()
                if not part:
                    continue
                xm = re.search(r'x=(\d+)', part, re.IGNORECASE)
                ym = re.search(r'y=(\d+)', part, re.IGNORECASE)
                if xm and ym:
                    pairs.append([int(xm.group(1)), int(ym.group(1))])
                else:
                    nums = re.findall(r'\d+', part)
                    if len(nums) >= 2:
                        pairs.append([int(nums[0]), int(nums[1])])

        logger.info(f"[captcha] Tọa độ ({len(pairs)} điểm): {pairs}")
        return pairs
    except Exception as e:
        logger.error(f"[captcha] Lỗi coordinates: {e}")
        return []


# ─────────────────────────────────────────────────────
# GỬI ẢNH LÊN 2CAPTCHA — GRID (lưới ô)
# ─────────────────────────────────────────────────────
def _solve_as_grid(img_path: str, rows: int = 3, cols: int = 3) -> list:
    """Gửi ảnh grid → trả list số thứ tự ô (1-indexed)"""
    if not solver:
        return []
    logger.info(f"[captcha] Gửi lên 2captcha (grid {rows}x{cols})...")
    try:
        result = solver.grid(
            img_path,
            hintText="Chọn tất cả các ô phù hợp với yêu cầu",
            rows=rows, cols=cols,
        )
        logger.info(f"[captcha] Grid result: {result}")
        code = result.get("code", "") if isinstance(result, dict) else str(result)
        code = re.sub(r'^click:', '', code, flags=re.IGNORECASE).strip()
        tiles = [int(x) for x in code.split(",") if x.strip().isdigit()]
        logger.info(f"[captcha] Ô cần click: {tiles}")
        return tiles
    except Exception as e:
        logger.error(f"[captcha] Lỗi grid: {e}")
        return []


# ─────────────────────────────────────────────────────
# CLICK CÁC TỌA ĐỘ (click-order)
# ─────────────────────────────────────────────────────
def _apply_coordinates(driver, coords: list, rect: dict):
    vp_w = driver.execute_script("return window.innerWidth;") or 1
    dpr  = Image.open(BytesIO(driver.get_screenshot_as_png())).width / vp_w

    _human_sleep(0.4, 0.9)
    for idx, (cx, cy) in enumerate(coords):
        jx = random.randint(-3, 3)
        jy = random.randint(-3, 3)
        px = int(rect["x"] + cx / dpr) + jx
        py = int(rect["y"] + cy / dpr) + jy
        logger.info(f"  [captcha] Click {idx+1}: crop({cx},{cy}) → vp({px},{py})")
        hold = random.randint(80, 180)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "  e.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));"
            "  e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}", px, py)
        time.sleep(hold / 1000)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "  e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "  e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}", px, py)
        _human_sleep(0.7, 1.5)
    _human_sleep(0.5, 1.0)


# ─────────────────────────────────────────────────────
# CLICK CÁC Ô GRID
# ─────────────────────────────────────────────────────
def _apply_grid_tiles(driver, tiles: list, rect: dict, count: int = 9):
    """Click vào ô grid theo tọa độ tính từ rect + vị trí ô."""
    rows = 3 if count > 6 else (2 if count > 4 else 2)
    cols = 3 if count > 6 else (3 if count > 4 else 2)
    cell_w = rect["w"] / cols
    cell_h = rect["h"] / rows

    for tile in tiles:
        idx = tile - 1  # 1-based → 0-based
        row = idx // cols
        col = idx % cols
        cx = rect["x"] + col * cell_w + cell_w / 2 + random.randint(-5, 5)
        cy = rect["y"] + row * cell_h + cell_h / 2 + random.randint(-5, 5)
        logger.info(f"  [captcha] Grid click ô {tile}: vp({cx:.0f},{cy:.0f})")
        _human_sleep(0.5, 1.1)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,"
            "clientX:arguments[0],clientY:arguments[1]}));}", cx, cy)
    _human_sleep(0.5, 1.0)


# ─────────────────────────────────────────────────────
# NHẤN NÚT OK / XÁC NHẬN
# ─────────────────────────────────────────────────────
def _press_ok(driver, rect: dict = None) -> bool:
    # Cách 1: JS tìm element text "OK" / "Xác nhận"
    clicked = driver.execute_script("""
        var txts = ['OK','ok','Xác nhận','Confirm','确认','Submit'];
        var els = Array.from(document.querySelectorAll('div,button,span,a'));
        for (var i = els.length - 1; i >= 0; i--) {
            var el = els[i];
            var txt = (el.innerText || el.textContent || '').trim();
            if (txts.indexOf(txt) >= 0 && el.offsetParent !== null) {
                el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
                el.click();
                return el.tagName + '/' + (el.className||'').toString().substring(0,40);
            }
        }
        return null;
    """)
    if clicked:
        logger.info(f"[captcha] OK clicked (JS text): {clicked}")
        _human_sleep(1.2, 2.0)
        return True

    # Cách 2: XPath
    for xp in ["//*[normalize-space(text())='OK']",
                "//*[contains(@class,'submit')]",
                "//*[contains(@class,'confirm')]"]:
        try:
            e = driver.find_element(By.XPATH, xp)
            if e.is_displayed():
                e.click()
                logger.info(f"[captcha] OK clicked (XPath): {xp}")
                _human_sleep(1.2, 2.0)
                return True
        except Exception:
            pass

    # Cách 3: Click tọa độ đoán trong modal
    if rect:
        for ry in [0.74, 0.78, 0.70, 0.82]:
            px = rect["x"] + rect["w"] * 0.50
            py = rect["y"] + rect["h"] * ry
            driver.execute_script(
                "var e=document.elementFromPoint(arguments[0],arguments[1]);"
                "if(e){e.dispatchEvent(new MouseEvent('click',{bubbles:true,"
                "clientX:arguments[0],clientY:arguments[1]}));}", px, py)
            _human_sleep(1.0, 1.5)
            # Kiểm tra modal đã biến mất chưa
            gone = _find_captcha_rect(driver, timeout=1) is None
            if gone:
                logger.info(f"[captcha] Modal đã tắt sau OK tại ry={ry}")
                return True

    logger.warning("[captcha] Không tìm được nút OK")
    return False


# ─────────────────────────────────────────────────────
# HÀM CHÍNH
# ─────────────────────────────────────────────────────
def solve_captcha_on_page(driver) -> bool:
    if not solver:
        logger.error("[captcha] solver=None ❌")
        return False

    MAX_ROUNDS = 6
    solved_count = 0

    for rnd in range(1, MAX_ROUNDS + 1):
        logger.info(f"[captcha] ── Vòng {rnd} ──────────────────────────")

        # 1. Chờ captcha xuất hiện
        rect = _find_captcha_rect(driver, timeout=6)

        if rect is None:
            if solved_count > 0:
                # Đã giải xong ít nhất 1 captcha → click ĐĂNG KÝ submit
                logger.info("[captcha] Không còn captcha → click ĐĂNG KÝ để submit form...")
                _human_sleep(1.5, 2.5)
                driver.execute_script("""
                    var btns = Array.from(document.querySelectorAll('div,button,span'))
                        .filter(function(e) {
                            return (e.innerText||'').trim()==='ĐĂNG KÝ' && e.offsetParent !== null;
                        });
                    if (btns.length > 0) {
                        var b = btns[btns.length - 1];
                        b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                        b.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
                        b.click();
                    }
                """)
                logger.info("[captcha] ĐĂNG KÝ đã click — chờ phản hồi...")
                _human_sleep(3.0, 4.0)
                # Reset để vòng tiếp theo kiểm tra captcha mới
                solved_count = 0
                continue
            else:
                logger.info("[captcha] Không tìm thấy captcha nào trên trang")
                break

        # 2. Detect loại captcha từ DOM
        ctype = _detect_type_from_dom(driver, rect)
        logger.info(f"[captcha] Loại: {ctype}")

        # 3. Chụp ảnh captcha
        img_path = _screenshot_rect_to_file(driver, rect)
        if not img_path:
            logger.error("[captcha] Không chụp được ảnh ❌")
            break

        try:
            if ctype == "grid":
                # Thử grid trước, fallback sang coordinates nếu lỗi
                tiles = _solve_as_grid(img_path)
                if tiles:
                    count = driver.execute_script("""
                        var sels = ['[class*="item"]','[class*="tile"]','[class*="cell"]','img'];
                        for (var s of sels) {
                            var n = document.querySelectorAll(s).length;
                            if (n >= 4) return n;
                        }
                        return 9;
                    """) or 9
                    _apply_grid_tiles(driver, tiles, rect, count)
                else:
                    logger.info("[captcha] Grid fail → fallback coordinates")
                    coords = _solve_as_coordinates(img_path)
                    if coords:
                        _apply_coordinates(driver, coords, rect)
            else:
                # click_order hoặc unknown → dùng coordinates (worker người thật)
                coords = _solve_as_coordinates(img_path)
                if not coords:
                    logger.warning("[captcha] Không lấy được tọa độ ❌")
                    break
                _apply_coordinates(driver, coords, rect)

        finally:
            try:
                os.unlink(img_path)
            except Exception:
                pass

        # 4. Nhấn OK
        ok = _press_ok(driver, rect)
        logger.info("[captcha] OK: " + ("✅" if ok else "⚠️ không nhấn được"))
        solved_count += 1
        _human_sleep(1.5, 2.5)

    else:
        logger.warning(f"[captcha] Hết {MAX_ROUNDS} vòng — thoát")

    return solved_count > 0

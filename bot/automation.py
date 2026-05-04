import time
import random
import string
import os
import base64
import logging
from selenium import webdriver

logger = logging.getLogger(__name__)

BASE_URL      = os.environ.get("BASE_URL", "")
WITHDRAW_PASS = os.environ.get("WITHDRAW_PASS", "")


def generate_random_user():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    user  = f"mem{suffix}"
    # fly88h.com dùng +84, ô nhập chỉ cần phần sau: 9xxxxxxxx
    phone = "9" + "".join(random.choices(string.digits, k=8))
    return user, phone


def generate_password():
    letters  = random.choices(string.ascii_letters, k=5)
    digits   = random.choices(string.digits, k=3)
    symbols  = random.choices("@#$!%&*", k=2)
    all_chars = letters + digits + symbols
    random.shuffle(all_chars)
    return "".join(all_chars)


def get_driver():
    from selenium.webdriver.chrome.service import Service

    # Ngẫu nhiên kích thước cửa sổ để tránh fingerprint cố định
    w = random.choice([1280, 1366, 1440, 1920])
    h = random.choice([768, 800, 900, 1080])

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={w},{h}")
    options.add_argument("--disable-extensions")

    # ── Chống phát hiện bot ──────────────────────────
    # Xóa flag "navigator.webdriver = true"
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Bỏ banner "Chrome is being controlled by automated software"
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # User-Agent Chrome thật (không có "HeadlessChrome")
    options.add_argument(
        f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/120.0.0.{random.randint(1,9999)} Safari/537.36"
    )

    chrome_bin = os.environ.get("CHROME_BIN", "")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "")
    if chromedriver_path:
        logger.info(f"Dùng chromedriver hệ thống: {chromedriver_path}")
        service = Service(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        logger.info("Dùng webdriver-manager để tìm chromedriver...")
        driver = webdriver.Chrome(options=options)

    # Ghi đè navigator.webdriver = false qua CDP
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN','vi','en-US','en']});
            window.chrome = { runtime: {} };
        """
    })

    return driver


def _screenshot_b64(driver) -> str:
    """Chụp ảnh màn hình, trả về base64 PNG."""
    try:
        return base64.b64encode(driver.get_screenshot_as_png()).decode()
    except Exception:
        return ""


def run_account_creation(
    full_name: str,
    stk: str,
    bank_name: str,
    progress_callback=None,
    on_credentials_ready=None,
    screenshot_callback=None,
):
    user, phone = generate_random_user()
    password = generate_password()
    result = {
        "username": user,
        "phone":    phone,
        "password": password,
        "steps":    [],
        "success":  False,
        "error":    None,
    }

    # Gửi thông tin đăng nhập ngay khi tạo (trước khi mở browser)
    if on_credentials_ready:
        on_credentials_ready(user, phone, password)

    driver = None
    try:
        if progress_callback:
            progress_callback("🚀 Khởi động trình duyệt...")

        driver = get_driver()

        # Lazy import captcha solver
        try:
            from captcha_solver import solve_captcha_on_page
            captcha_enabled = True
        except Exception as e:
            logger.warning(f"Captcha solver không khả dụng: {e}")
            captcha_enabled = False

        # ── BƯỚC 1: ĐĂNG KÝ ──────────────────────────────────────
        if progress_callback:
            progress_callback(f"📝 Bước 1: Điền form đăng ký [{user}]...")

        driver.get(f"{BASE_URL}/home/register")
        time.sleep(3)

        # 1. Điền tất cả trường
        driver.execute_script(f"""
            function fillField(p, v) {{
                let input = document.querySelector(`input[placeholder*="${{p}}"]`);
                if (input) {{
                    input.value = v;
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}
            fillField("Tên tài khoản", "{user}");
            fillField("mật khẩu", "{password}");
            fillField("SĐT", "{phone}");
            fillField("Họ và Tên", "{full_name.upper()}");
            let cb = document.querySelector('input[type="checkbox"]');
            if (cb && !cb.checked) cb.click();
        """)
        time.sleep(2)

        # 2. Nhấn ĐĂNG KÝ lần đầu → kích hoạt hiện captcha
        # Dùng phần tử CUỐI cùng vì trang có 2 "ĐĂNG KÝ": tab trên cùng + nút submit dưới form
        if progress_callback:
            progress_callback("🖱️ Nhấn ĐĂNG KÝ (nút xác nhận) để hiện captcha...")
        driver.execute_script("""
            let cb = document.querySelector('input[type="checkbox"]');
            if (cb && !cb.checked) cb.click();
            setTimeout(() => {
                let all = Array.from(document.querySelectorAll('div, button, span'))
                    .filter(e => e.innerText && e.innerText.trim() === 'ĐĂNG KÝ' && e.offsetParent !== null);
                if (all.length > 0) {
                    let btn = all[all.length - 1];   // Nút CUỐI = nút xác nhận dưới form
                    const opt = { bubbles: true, cancelable: true, view: window };
                    btn.dispatchEvent(new MouseEvent('mousedown', opt));
                    btn.dispatchEvent(new MouseEvent('mouseup',   opt));
                    btn.click();
                }
            }, 800);
        """)
        time.sleep(3)   # Chờ captcha xuất hiện

        # 3. Giải captcha — retry tối đa 3 lần
        MAX_CAPTCHA_RETRY = 3
        solved = False

        if captcha_enabled:
            for attempt in range(1, MAX_CAPTCHA_RETRY + 1):
                if progress_callback:
                    progress_callback(f"🧩 Đang giải captcha... (lần {attempt}/{MAX_CAPTCHA_RETRY})")
                try:
                    solved = solve_captcha_on_page(driver)
                except Exception as e:
                    logger.warning(f"Captcha lần {attempt} lỗi: {e}")
                    solved = False

                if solved:
                    status = f"✅ Captcha: giải thành công (lần {attempt})"
                    result["steps"].append(status)
                    if progress_callback:
                        progress_callback(status)
                    break

                # Kiểm tra đã thoát trang đăng ký chưa (captcha có thể đã qua)
                if "register" not in driver.current_url:
                    solved = True
                    result["steps"].append("✅ Captcha: đã qua (redirect)")
                    break

                if attempt < MAX_CAPTCHA_RETRY:
                    if progress_callback:
                        progress_callback(f"⚠️ Lần {attempt} chưa được, thử lại sau 3 giây...")
                    time.sleep(3)
                    # Nhấn ĐĂNG KÝ lại để hiện captcha mới
                    driver.execute_script("""
                        let cb = document.querySelector('input[type="checkbox"]');
                        if (cb && !cb.checked) cb.click();
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
                    time.sleep(3)
                else:
                    result["steps"].append(f"⚠️ Captcha: thất bại sau {MAX_CAPTCHA_RETRY} lần thử")
                    if progress_callback:
                        progress_callback(f"❌ Captcha thất bại sau {MAX_CAPTCHA_RETRY} lần")
        else:
            if progress_callback:
                progress_callback("🧩 Chờ captcha hiện & giải tay (20 giây)...")
            time.sleep(20)
            result["steps"].append("⏳ Captcha: chờ 20 giây")

        time.sleep(2)   # Chờ captcha được xử lý xong

        # ── XÁC NHẬN ĐĂNG KÝ THÀNH CÔNG ──────────────────────────
        current_url = driver.current_url
        page_text   = driver.find_element("tag name", "body").text[:500]

        # Chụp ảnh gửi Telegram để debug
        if screenshot_callback:
            screenshot_callback(_screenshot_b64(driver), "📸 Sau bước đăng ký")

        if "register" in current_url:
            # Vẫn còn trên trang đăng ký → thất bại
            error_el = driver.execute_script(
                "return Array.from(document.querySelectorAll('.van-toast, .error, [class*=error], [class*=toast]'))"
                ".map(e=>e.innerText).filter(Boolean).join(' | ');"
            )
            err_msg = error_el or "Vẫn ở trang đăng ký (có thể captcha sai hoặc tên đã tồn tại)"
            result["steps"].append(f"❌ Bước 1: {err_msg}")
            result["error"] = err_msg
            return result
        else:
            result["steps"].append(f"✅ Bước 1: Đăng ký thành công (URL: {current_url.split('/')[-1]})")

        # ── BƯỚC 2: CÀI MÃ PIN ───────────────────────────────────
        if progress_callback:
            progress_callback("🔒 Bước 2: Cài đặt mã PIN bảo mật...")

        driver.get(f"{BASE_URL}/home/security?active=5")
        time.sleep(3)

        driver.execute_script(f"""
            async function setPin() {{
                function realClick(el) {{
                    const opt = {{ bubbles: true, cancelable: true, view: window }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opt));
                    el.dispatchEvent(new TouchEvent('touchstart', opt));
                    el.dispatchEvent(new MouseEvent('mouseup', opt));
                    el.click();
                }}
                let area = document.querySelector('.van-password-input') || document.querySelector('ul');
                if (area) realClick(area);
                await new Promise(r => setTimeout(r, 800));
                for (let n of "{WITHDRAW_PASS}{WITHDRAW_PASS}") {{
                    let k = Array.from(document.querySelectorAll('i, span, div, li')).find(
                        e => e.textContent.trim() === n && e.offsetParent !== null);
                    if (k) {{ realClick(k); await new Promise(r => setTimeout(r, 200)); }}
                }}
                setTimeout(() => {{
                    let ok = Array.from(document.querySelectorAll('div, button')).find(
                        e => e.innerText && e.innerText.trim() === 'Xác Nhận');
                    if (ok) ok.click();
                }}, 500);
            }}
            setPin();
        """)
        time.sleep(4)

        if screenshot_callback:
            screenshot_callback(_screenshot_b64(driver), "📸 Sau bước cài PIN")

        result["steps"].append("✅ Bước 2: Cài mã PIN xong")

        # ── BƯỚC 3: LIÊN KẾT NGÂN HÀNG ───────────────────────────
        if progress_callback:
            progress_callback(f"🏦 Bước 3: Liên kết ngân hàng {bank_name}...")

        driver.get(f"{BASE_URL}/home/withdraw?active=10")
        time.sleep(3)

        driver.execute_script(f"""
            async function linkBank() {{
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                function powerTouch(el) {{
                    const opt = {{ bubbles: true, cancelable: true, view: window }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opt));
                    el.dispatchEvent(new TouchEvent('touchstart', opt));
                    el.dispatchEvent(new TouchEvent('touchend', opt));
                    el.dispatchEvent(new MouseEvent('mouseup', opt));
                    el.click();
                }}
                let btnAdd = Array.from(document.querySelectorAll('div, span')).find(
                    e => e.innerText && e.innerText.trim() === 'Thêm Vào');
                if (btnAdd) powerTouch(btnAdd);
                await sleep(1200);
                for (let n of "{WITHDRAW_PASS}") {{
                    let k = Array.from(document.querySelectorAll('i, span, div, li')).find(
                        e => e.textContent.trim() === n && e.offsetParent !== null);
                    if (k) {{ powerTouch(k); await sleep(200); }}
                }}
                await sleep(1000);
                let next = Array.from(document.querySelectorAll('div, button')).find(
                    e => e.innerText && e.innerText.trim() === 'Tiếp theo');
                if (next) powerTouch(next);
                await sleep(2500);
                let inp = document.querySelector('input[placeholder*="số tài khoản"]')
                       || document.querySelector('input[placeholder*="Số tài khoản"]');
                if (inp) {{
                    let tracker = inp._valueTracker;
                    if (tracker) tracker.setValue(inp.value);
                    inp.value = "{stk}";
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                let sel = Array.from(document.querySelectorAll('div, span')).find(
                    e => e.innerText && e.innerText.includes('Chọn ngân hàng'));
                if (sel) {{
                    powerTouch(sel);
                    await sleep(1500);
                    let target = Array.from(document.querySelectorAll('.van-picker-column__item, span')).find(
                        e => e.innerText && e.innerText.trim() === "{bank_name}");
                    if (target) {{
                        powerTouch(target);
                        await sleep(600);
                        let ok = Array.from(document.querySelectorAll('button, div')).find(
                            e => e.innerText && e.innerText.trim() === 'Xác nhận');
                        if (ok) powerTouch(ok);
                    }}
                }}
                await sleep(1500);
                let final = Array.from(document.querySelectorAll('div, button')).find(
                    e => e.innerText && e.innerText.trim() === 'Xác Nhận');
                if (final) powerTouch(final);
            }}
            linkBank();
        """)
        time.sleep(5)

        if screenshot_callback:
            screenshot_callback(_screenshot_b64(driver), "📸 Sau bước liên kết ngân hàng")

        result["steps"].append(f"✅ Bước 3: Liên kết {bank_name} xong")
        result["success"] = True

    except Exception as e:
        logger.error(f"Lỗi automation: {e}")
        result["error"] = str(e)
        if driver and screenshot_callback:
            screenshot_callback(_screenshot_b64(driver), f"📸 Lỗi: {e}")
    finally:
        if driver:
            driver.quit()

    return result

import time
import random
import string
import os
import logging
from selenium import webdriver

logger = logging.getLogger(__name__)

BASE_URL      = os.environ.get("BASE_URL", "")
WITHDRAW_PASS = os.environ.get("WITHDRAW_PASS", "")


def generate_random_user():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    user  = f"mem{suffix}"
    # Site dùng prefix +84, ô nhập chỉ cần phần sau: 9xxxxxxxx (9 chữ số)
    phone = "9" + "".join(random.choices(string.digits, k=8))
    return user, phone


def generate_password():
    """
    Tạo mật khẩu ngẫu nhiên 10 ký tự, đảm bảo có đủ:
    chữ cái + số + ký hiệu (thỏa mãn yêu cầu 6-16 ký tự, 2+ loại ký tự)
    """
    letters  = random.choices(string.ascii_letters, k=5)
    digits   = random.choices(string.digits, k=3)
    symbols  = random.choices("@#$!%&*", k=2)
    all_chars = letters + digits + symbols
    random.shuffle(all_chars)
    return "".join(all_chars)


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_bin = os.environ.get("CHROME_BIN", "")
    if chrome_bin:
        options.binary_location = chrome_bin
    return webdriver.Chrome(options=options)


def run_account_creation(
    full_name: str,
    stk: str,
    bank_name: str,
    progress_callback=None,
    on_credentials_ready=None,
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

    # Gửi thông tin đăng nhập ngay khi tạo ra (trước khi chạy trình duyệt)
    if on_credentials_ready:
        on_credentials_ready(user, phone, password)

    driver = None
    try:
        if progress_callback:
            progress_callback("🚀 Khởi động trình duyệt...")

        driver = get_driver()

        # Import captcha solver (lazy — không crash nếu chưa có API key)
        try:
            from captcha_solver import solve_captcha_on_page
            captcha_enabled = True
        except Exception as e:
            logger.warning(f"Captcha solver không khả dụng: {e}")
            captcha_enabled = False

        # ── BƯỚC 1: ĐĂNG KÝ ──────────────────────────────────────
        if progress_callback:
            progress_callback(f"📝 Bước 1: Điền form đăng ký cho [{user}]...")

        driver.get(f"{BASE_URL}/home/register")
        time.sleep(3)

        js_step1 = f"""
            function fillField(p, v) {{
                let input = document.querySelector(`input[placeholder*="${{p}}"]`);
                if (input) {{
                    input.value = v;
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}
            function realClick(el) {{
                if (!el) return;
                const opt = {{ bubbles: true, cancelable: true, view: window }};
                el.dispatchEvent(new MouseEvent('mousedown', opt));
                el.dispatchEvent(new MouseEvent('mouseup', opt));
                el.click();
            }}

            fillField("Tên tài khoản", "{user}");
            fillField("mật khẩu", "{password}");
            fillField("SĐT", "{phone}");
            fillField("Họ và Tên", "{full_name.upper()}");

            setTimeout(() => {{
                let cb = document.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) realClick(cb);
            }}, 400);
        """
        driver.execute_script(js_step1)
        time.sleep(2)

        # ── XỬ LÝ CAPTCHA ─────────────────────────────────────────
        if captcha_enabled:
            if progress_callback:
                progress_callback("🧩 AI đang giải captcha...")
            try:
                solved = solve_captcha_on_page(driver)
                if solved:
                    result["steps"].append("✅ Captcha: AI giải thành công")
                    if progress_callback:
                        progress_callback("✅ Captcha đã giải xong!")
                else:
                    result["steps"].append("⚠️ Không tìm thấy captcha, tiếp tục")
            except Exception as e:
                logger.error(f"Lỗi giải captcha: {e}")
                result["steps"].append(f"⚠️ Captcha lỗi: {e}")
        else:
            if progress_callback:
                progress_callback("🧩 Chờ captcha (15 giây)...")
            time.sleep(15)
            result["steps"].append("⏳ Captcha: chờ thủ công 15 giây")

        # Nhấn ĐĂNG KÝ
        driver.execute_script("""
            let btn = Array.from(document.querySelectorAll('div, button')).find(
                e => e.innerText && e.innerText.trim() === 'ĐĂNG KÝ');
            if (btn) {
                const opt = { bubbles: true, cancelable: true, view: window };
                btn.dispatchEvent(new MouseEvent('mousedown', opt));
                btn.dispatchEvent(new MouseEvent('mouseup', opt));
                btn.click();
            }
        """)
        time.sleep(3)
        result["steps"].append("✅ Bước 1: Đăng ký xong")

        # ── BƯỚC 2: CÀI MÃ PIN ───────────────────────────────────
        if progress_callback:
            progress_callback("🔒 Bước 2: Cài đặt mã PIN bảo mật...")

        driver.get(f"{BASE_URL}/home/security?active=5")
        time.sleep(3)

        js_step2 = f"""
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
        """
        driver.execute_script(js_step2)
        time.sleep(4)
        result["steps"].append("✅ Bước 2: Cài mã PIN xong")

        # ── BƯỚC 3: LIÊN KẾT NGÂN HÀNG ───────────────────────────
        if progress_callback:
            progress_callback(f"🏦 Bước 3: Liên kết ngân hàng {bank_name}...")

        driver.get(f"{BASE_URL}/home/withdraw?active=10")
        time.sleep(3)

        js_step3 = f"""
            async function linkBank() {{
                const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                function powerTouch(el) {{
                    const opt = {{ bubbles: true, cancelable: true, view: window }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opt));
                    el.dispatchEvent(new TouchEvent('touchstart', opt));
                    el.dispatchEvent(new TouchEvent('touchend', opt));
                    el.dispatchEvent(new MouseEvent('mouseup', opt));
                    el.click();
                }}

                // Nhấn "Thêm Vào"
                let btnAdd = Array.from(document.querySelectorAll('div, span')).find(
                    e => e.innerText && e.innerText.trim() === 'Thêm Vào');
                if (btnAdd) powerTouch(btnAdd);
                await sleep(1200);

                // Nhập PIN để xác thực
                for (let n of "{WITHDRAW_PASS}") {{
                    let k = Array.from(document.querySelectorAll('i, span, div, li')).find(
                        e => e.textContent.trim() === n && e.offsetParent !== null);
                    if (k) {{ powerTouch(k); await sleep(200); }}
                }}
                await sleep(1000);

                // Tiếp theo
                let next = Array.from(document.querySelectorAll('div, button')).find(
                    e => e.innerText && e.innerText.trim() === 'Tiếp theo');
                if (next) powerTouch(next);
                await sleep(2500);

                // Điền số tài khoản (dùng valueTracker cho React)
                let inp = document.querySelector('input[placeholder*="số tài khoản"]')
                       || document.querySelector('input[placeholder*="Số tài khoản"]');
                if (inp) {{
                    let tracker = inp._valueTracker;
                    if (tracker) tracker.setValue(inp.value);
                    inp.value = "{stk}";
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}

                // Chọn ngân hàng từ picker
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

                // Xác nhận cuối
                let final = Array.from(document.querySelectorAll('div, button')).find(
                    e => e.innerText && e.innerText.trim() === 'Xác Nhận');
                if (final) powerTouch(final);
            }}
            linkBank();
        """
        driver.execute_script(js_step3)
        time.sleep(5)
        result["steps"].append(f"✅ Bước 3: Liên kết {bank_name} xong")

        result["success"] = True

    except Exception as e:
        logger.error(f"Lỗi automation: {e}")
        result["error"] = str(e)
    finally:
        if driver:
            driver.quit()

    return result

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
    user   = f"user{suffix}"
    phone  = "8" + "".join(random.choices(string.digits, k=8))
    return user, phone


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    )
    chrome_bin = os.environ.get("CHROME_BIN", "")
    if chrome_bin:
        options.binary_location = chrome_bin
    return webdriver.Chrome(options=options)


def run_account_creation(full_name: str, stk: str, bank_name: str, progress_callback=None):
    user, phone = generate_random_user()
    result = {
        "username": user,
        "phone":    phone,
        "password": "MatKhauManh123@",
        "steps":    [],
        "success":  False,
        "error":    None,
    }

    driver = None
    try:
        if progress_callback:
            progress_callback("🚀 Khởi động trình duyệt...")

        driver = get_driver()

        # ── BƯỚC 1: ĐĂNG KÝ ──────────────────────────────────────
        if progress_callback:
            progress_callback(f"📝 Bước 1: Điền form đăng ký cho [{user}]...")

        driver.get(f"{BASE_URL}/home/register")
        time.sleep(3)

        js_register = f"""
            function powerTouch(el) {{
                if (!el) return;
                const opt = {{ bubbles: true, cancelable: true, view: window }};
                el.dispatchEvent(new PointerEvent('pointerdown', opt));
                el.dispatchEvent(new MouseEvent('mousedown', opt));
                el.dispatchEvent(new PointerEvent('pointerup', opt));
                el.dispatchEvent(new MouseEvent('mouseup', opt));
                el.click();
            }}
            function fillField(placeholder, val) {{
                let input = document.querySelector(`input[placeholder*="${{placeholder}}"]`);
                if (input) {{
                    input.value = val;
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }}
            fillField("Tên tài khoản", "{user}");
            fillField("mật khẩu", "MatKhauManh123@");
            fillField("SĐT", "{phone}");
            fillField("Họ và Tên", "{full_name}");
            setTimeout(() => {{
                let cb = document.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) powerTouch(cb);
                let regBtn = Array.from(document.querySelectorAll('div, button')).find(
                    el => el.innerText.trim() === 'ĐĂNG KÝ');
                if (regBtn) powerTouch(regBtn);
            }}, 500);
        """
        driver.execute_script(js_register)
        result["steps"].append("✅ Bước 1: Điền form đăng ký xong")

        if progress_callback:
            progress_callback("🧩 Chờ xử lý captcha (15 giây)...")
        time.sleep(15)

        # ── BƯỚC 2: CÀI MÃ PIN ───────────────────────────────────
        if progress_callback:
            progress_callback("🔒 Bước 2: Cài đặt mã PIN bảo mật...")

        driver.get(f"{BASE_URL}/home/security?active=5")
        time.sleep(3)

        js_setup_pass = f"""
            async function setupPass() {{
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                function powerTouch(el) {{
                    if (!el) return;
                    const opt = {{ bubbles: true, cancelable: true, view: window }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opt));
                    el.dispatchEvent(new TouchEvent('touchstart', opt));
                    el.dispatchEvent(new MouseEvent('mousedown', opt));
                    el.dispatchEvent(new PointerEvent('pointerup', opt));
                    el.dispatchEvent(new TouchEvent('touchend', opt));
                    el.dispatchEvent(new MouseEvent('mouseup', opt));
                    el.click();
                }}
                let inputArea = document.querySelector('.van-password-input') || document.querySelector('ul');
                if (inputArea) powerTouch(inputArea);
                await sleep(800);
                const pass = "{WITHDRAW_PASS}";
                for (let num of (pass + pass)) {{
                    let key = Array.from(document.querySelectorAll('i, span, div, li')).find(
                        el => el.textContent.trim() === num && el.offsetParent !== null);
                    if (key) {{ powerTouch(key); await sleep(200); }}
                }}
                await sleep(500);
                let confirm = Array.from(document.querySelectorAll('div, button')).find(
                    el => el.innerText.trim() === 'Xác Nhận');
                if (confirm) powerTouch(confirm);
            }}
            setupPass();
        """
        driver.execute_script(js_setup_pass)
        time.sleep(4)
        result["steps"].append("✅ Bước 2: Cài mã PIN xong")

        # ── BƯỚC 3: THÊM TÀI KHOẢN NGÂN HÀNG ────────────────────
        if progress_callback:
            progress_callback(f"🏦 Bước 3: Thêm ngân hàng {bank_name}...")

        driver.get(f"{BASE_URL}/home/security?active=4")
        time.sleep(3)

        js_add_bank = f"""
            async function addBank() {{
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                function powerTouch(el) {{
                    if (!el) return;
                    const opt = {{ bubbles: true, cancelable: true, view: window }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opt));
                    el.dispatchEvent(new MouseEvent('mousedown', opt));
                    el.dispatchEvent(new PointerEvent('pointerup', opt));
                    el.dispatchEvent(new MouseEvent('mouseup', opt));
                    el.click();
                }}
                function fillField(placeholder, val) {{
                    let input = document.querySelector(`input[placeholder*="${{placeholder}}"]`);
                    if (input) {{
                        input.value = val;
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
                let addBtn = Array.from(document.querySelectorAll('div, button, span')).find(
                    el => el.innerText && el.innerText.trim().includes('Thêm'));
                if (addBtn) powerTouch(addBtn);
                await sleep(1500);
                fillField("Tên ngân hàng", "{bank_name}");
                fillField("Số tài khoản", "{stk}");
                fillField("Họ và Tên", "{full_name}");
                await sleep(500);
                let confirmBtn = Array.from(document.querySelectorAll('div, button')).find(
                    el => el.innerText && (
                        el.innerText.trim() === 'Xác Nhận' ||
                        el.innerText.trim() === 'LƯU' ||
                        el.innerText.trim() === 'Lưu'
                    ));
                if (confirmBtn) powerTouch(confirmBtn);
            }}
            addBank();
        """
        driver.execute_script(js_add_bank)
        time.sleep(4)
        result["steps"].append(f"✅ Bước 3: Thêm {bank_name} xong")

        result["success"] = True

    except Exception as e:
        logger.error(f"Lỗi automation: {e}")
        result["error"] = str(e)
    finally:
        if driver:
            driver.quit()

    return result

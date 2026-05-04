"""
Captcha solver toàn diện — dùng 2captcha SDK.

Hỗ trợ đầy đủ:
  Token-based (không cần ảnh — tốt nhất, nhanh nhất):
    GeeTest v4, GeeTest v3, reCAPTCHA v2/v3/v2-Enterprise/v3-Enterprise,
    hCaptcha, FunCaptcha/Arkose Labs, Cloudflare Turnstile,
    Capy Puzzle, Amazon WAF CAPTCHA, CyberSiARA, MTCaptcha,
    Cutcaptcha, Friendly Captcha, DataDome, atbCAPTCHA,
    Tencent, Lemin, KeyCAPTCHA, Prosopo Procaptcha,
    CaptchaFox, VK Captcha, Temu Captcha, Altcha, Yandex Smart

  Image-based (chụp ảnh gửi lên 2captcha worker):
    Normal CAPTCHA, Text CAPTCHA, Rotate, Grid,
    Coordinates, Draw Around, Bounding Box, Audio CAPTCHA

Luồng chính (solve_captcha_on_page):
  1. Chờ captcha container xuất hiện
  2. Phát hiện loại captcha (check_captcha_type)
  3a. Token-based → extract sitekey → gọi 2captcha → inject token → submit
  3b. Image-based  → chụp ảnh → gửi 2captcha worker → apply tọa độ/tiles
  4. Lặp tối đa MAX_ROUNDS vòng
"""
import os, re, time, random, logging, tempfile
from io import BytesIO
from PIL import Image
from selenium.webdriver.common.by import By
from twocaptcha import TwoCaptcha

logger = logging.getLogger(__name__)

API_KEY          = os.getenv("TWOCAPTCHA_API_KEY")
BASE_URL         = os.getenv("BASE_URL", "https://fly88h.com")
GEETEST_V4_ID    = os.getenv("GEETEST_V4_CAPTCHA_ID", "cff289689d0273ca771b5c1ef63dc8db")
REGISTER_URL     = f"{BASE_URL}/home/register"
REGISTER_API_URL = os.getenv("REGISTER_API_URL", f"{BASE_URL}/api/v1/register")

solver = TwoCaptcha(API_KEY) if API_KEY else None
if not API_KEY:
    logger.error("Không tìm thấy TWOCAPTCHA_API_KEY ❌")


# ═══════════════════════════════════════════════════════════
# TIỆN ÍCH
# ═══════════════════════════════════════════════════════════
def _human_sleep(min_s: float, max_s: float):
    time.sleep(random.uniform(min_s, max_s))


def _screenshot_b64(driver) -> str:
    import base64
    try:
        return base64.b64encode(driver.get_screenshot_as_png()).decode()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════
# TÌM CAPTCHA CONTAINER
# ═══════════════════════════════════════════════════════════
_CAPTCHA_SELECTORS = [
    # GeeTest v4
    '[class*="geetest4_wind"]', '[class*="geetest4_holder"]',
    '[class*="geetest4_box"]',  '[class*="geetest4"]', '[id*="geetest4"]',
    # GeeTest v3
    '[class*="geetest_wind"]',  '[class*="geetest_holder"]',
    '[class*="geetest"]',       '[id*="geetest"]',
    # reCAPTCHA / hCaptcha
    '.g-recaptcha', '.h-captcha', 'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    # FunCaptcha / Arkose
    '[id*="funcaptcha"]', '[class*="funcaptcha"]',
    '[id*="arkose"]', '[class*="arkose"]',
    'iframe[src*="arkoselabs"]', 'iframe[src*="funcaptcha"]',
    # Turnstile
    '.cf-turnstile', '[class*="turnstile"]',
    'iframe[src*="challenges.cloudflare"]',
    # Generic captcha containers
    '[class*="captcha-modal"]', '[class*="captcha_modal"]',
    '[class*="captcha-container"]', '[class*="captcha-wrap"]',
    '[id*="captcha"]', '[class*="captcha"]',
    '[class*="verify-wrap"]', '[class*="verify-modal"]',
    # Capy, DataDome, others
    '[class*="capy"]', '[id*="capy"]',
    '[class*="lemin"]', '[id*="lemin"]',
    '[class*="mtcaptcha"]',
    # Audio
    '[class*="audio-captcha"]', '[id*="audioCaptcha"]',
]


def _find_captcha_rect(driver, timeout: int = 8) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = driver.execute_script("""
            var selectors = arguments[0];
            for (var i = 0; i < selectors.length; i++) {
                var s = selectors[i];
                try {
                    var els = document.querySelectorAll(s);
                    for (var j = 0; j < els.length; j++) {
                        var el = els[j];
                        var r = el.getBoundingClientRect();
                        if (r.width > 60 && r.height > 40 &&
                            r.top >= 0 && r.top < window.innerHeight) {
                            return {
                                x: r.left, y: r.top, w: r.width, h: r.height,
                                selector: s, tag: el.tagName,
                                cls: (el.className||'').toString().substring(0,80)
                            };
                        }
                    }
                } catch(e) {}
            }
            return null;
        """, _CAPTCHA_SELECTORS)

        if info:
            logger.info(
                f"[captcha] Tìm thấy: sel='{info['selector']}' "
                f"cls='{info['cls'][:60]}' size={info['w']:.0f}x{info['h']:.0f}"
            )
            return info
        time.sleep(0.6)

    logger.info("[captcha] Không tìm thấy captcha container sau %ds", timeout)
    return None


# ═══════════════════════════════════════════════════════════
# CHỤP ẢNH CAPTCHA → FILE TẠM
# ═══════════════════════════════════════════════════════════
def _screenshot_rect_to_file(driver, rect: dict) -> str | None:
    try:
        full_png = driver.get_screenshot_as_png()
        img  = Image.open(BytesIO(full_png))
        vp_w = driver.execute_script("return window.innerWidth;") or 1
        dpr  = img.width / vp_w
        pad  = 8
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


# ═══════════════════════════════════════════════════════════
# PHÁT HIỆN LOẠI CAPTCHA ĐẦY ĐỦ
# ═══════════════════════════════════════════════════════════
def check_captcha_type(driver, rect: dict = None) -> dict:
    """
    Phân tích DOM để xác định loại captcha.
    Trả về dict: {type, version, engine, subtype, confidence, evidence, cls}
    """
    info = driver.execute_script("""
        var rx = arguments[0], ry = arguments[1],
            rw = arguments[2], rh = arguments[3];

        var bodyHTML = document.body ? document.body.innerHTML.toLowerCase() : '';
        var bodyText = document.body ? document.body.innerText.toLowerCase() : '';

        var rootEl = null;
        if (rx !== null) {
            var el = document.elementFromPoint(rx + rw/2, ry + rh/2);
            if (el) {
                rootEl = el;
                for (var i = 0; i < 25; i++) {
                    if (!rootEl.parentElement) break;
                    var cls2 = (rootEl.parentElement.className||'').toString().toLowerCase();
                    if (/geetest|captcha|verify|recaptcha|hcaptcha|arkose|turnstile|capy|lemin|funcap/.test(cls2)) {
                        rootEl = rootEl.parentElement;
                    } else break;
                }
            }
        }
        var rootCls = rootEl ? (rootEl.className||'').toString().toLowerCase() : '';
        var rootHTML = rootEl ? rootEl.innerHTML.toLowerCase() : bodyHTML.substring(0,8000);

        function has(str) { return rootCls.includes(str)||bodyHTML.includes(str)||rootHTML.includes(str); }
        function hasURL(str) { return bodyHTML.includes(str); }
        function countEls(sel) { try{return document.querySelectorAll(sel).length;}catch(e){return 0;} }

        // ── GeeTest v4 ────────────────────────────────────────
        if (has('geetest4_')) {
            if (has('geetest4_slide')||has('geetest4_arrow')||countEls('[class*="geetest4_slider"]')>0||countEls('[class*="geetest4_drag"]')>0)
                return {type:'geetest_v4_slide',version:'v4',engine:'geetest',subtype:'slide',confidence:95,evidence:'geetest4_slide/drag',cls:rootCls};
            if (has('geetest4_grid')||has('geetest4_tile')||countEls('[class*="geetest4_item"]')>=4)
                return {type:'geetest_v4_grid',version:'v4',engine:'geetest',subtype:'grid',confidence:95,evidence:'geetest4_grid/tile',cls:rootCls};
            return {type:'geetest_v4_click',version:'v4',engine:'geetest',subtype:'click',confidence:85,evidence:'geetest4_ default',cls:rootCls};
        }

        // ── GeeTest v3 ────────────────────────────────────────
        if (has('geetest_') && !has('geetest4_')) {
            if (has('geetest_slider')||has('geetest_drag')||countEls('[class*="geetest_slider"]')>0)
                return {type:'geetest_v3_slide',version:'v3',engine:'geetest',subtype:'slide',confidence:92,evidence:'geetest_slide',cls:rootCls};
            if (has('geetest_click')||has('geetest_icon')||countEls('[class*="geetest_item"]')>=3)
                return {type:'geetest_v3_click',version:'v3',engine:'geetest',subtype:'click',confidence:90,evidence:'geetest_click',cls:rootCls};
            return {type:'geetest_v3_slide',version:'v3',engine:'geetest',subtype:'slide',confidence:75,evidence:'geetest_ default',cls:rootCls};
        }

        // ── FunCaptcha / Arkose Labs ──────────────────────────
        if (hasURL('arkoselabs.com')||hasURL('funcaptcha')||hasURL('arkose')||
            countEls('iframe[src*="arkoselabs"]')>0||countEls('iframe[src*="funcaptcha"]')>0||
            has('fc-token')||has('arkose-labs')||countEls('[id*="arkose"]')>0) {
            if (has('grid')||has('matching')||countEls('[class*="challenge-grid"]')>0)
                return {type:'funcaptcha_grid',version:'',engine:'funcaptcha',subtype:'grid',confidence:90,evidence:'funcaptcha grid',cls:rootCls};
            if (has('compare')||has('matching'))
                return {type:'funcaptcha_compare',version:'',engine:'funcaptcha',subtype:'compare',confidence:88,evidence:'funcaptcha compare',cls:rootCls};
            return {type:'funcaptcha',version:'',engine:'funcaptcha',subtype:'',confidence:90,evidence:'arkoselabs/funcaptcha',cls:rootCls};
        }

        // ── Cloudflare Turnstile ──────────────────────────────
        if (has('cf-turnstile')||hasURL('challenges.cloudflare.com')||has('turnstile')||
            countEls('iframe[src*="challenges.cloudflare"]')>0||
            countEls('.cf-turnstile')>0) {
            return {type:'turnstile',version:'',engine:'cloudflare',subtype:'',confidence:95,evidence:'cf-turnstile/cloudflare',cls:rootCls};
        }

        // ── reCAPTCHA (v2 / v3 / Enterprise) ─────────────────
        if (hasURL('recaptcha')||has('g-recaptcha')||countEls('iframe[src*="recaptcha"]')>0||countEls('.g-recaptcha')>0) {
            var isV3 = bodyHTML.includes('grecaptcha.execute')||bodyHTML.includes("'v3'")||bodyHTML.includes('"v3"');
            var isEnt = bodyHTML.includes('enterprise.js')||bodyHTML.includes('enterprise/v3');
            var version = isV3 ? 'v3' : 'v2';
            var enterprise = isEnt;
            return {type: enterprise ? (isV3?'recaptcha_v3_enterprise':'recaptcha_v2_enterprise') : (isV3?'recaptcha_v3':'recaptcha_v2'),
                    version:version,engine:'recaptcha',subtype:'checkbox',confidence:90,
                    evidence:'recaptcha'+(isEnt?' enterprise':'')+(isV3?' v3':' v2'),cls:rootCls};
        }

        // ── hCaptcha ──────────────────────────────────────────
        if (hasURL('hcaptcha')||has('h-captcha')||countEls('iframe[src*="hcaptcha"]')>0||countEls('.h-captcha')>0)
            return {type:'hcaptcha',version:'',engine:'hcaptcha',subtype:'',confidence:95,evidence:'hcaptcha',cls:rootCls};

        // ── Capy Puzzle ───────────────────────────────────────
        if (hasURL('capy.me')||has('capy-captcha')||has('puzzle_prompts')||countEls('[id*="capy"]')>0)
            return {type:'capy',version:'',engine:'capy',subtype:'puzzle',confidence:90,evidence:'capy puzzle',cls:rootCls};

        // ── Amazon WAF ────────────────────────────────────────
        if (hasURL('awswaf')||hasURL('aws-waf')||has('aws-waf-token')||hasURL('captcha.us-east-1.awswaf'))
            return {type:'amazon_waf',version:'',engine:'amazon',subtype:'',confidence:92,evidence:'amazon waf',cls:rootCls};

        // ── DataDome ──────────────────────────────────────────
        if (hasURL('datadome.co')||hasURL('datadome')||has('dd_token')||countEls('[id*="datadome"]')>0)
            return {type:'datadome',version:'',engine:'datadome',subtype:'',confidence:90,evidence:'datadome',cls:rootCls};

        // ── MTCaptcha ─────────────────────────────────────────
        if (hasURL('mtcaptcha.com')||has('mtcaptcha')||countEls('[class*="mtcaptcha"]')>0||countEls('#mtcaptcha')>0)
            return {type:'mtcaptcha',version:'',engine:'mtcaptcha',subtype:'',confidence:92,evidence:'mtcaptcha',cls:rootCls};

        // ── Tencent ───────────────────────────────────────────
        if (has('tcaptcha')||has('tc_appid')||hasURL('captcha.qq.com')||hasURL('TCaptcha'))
            return {type:'tencent',version:'',engine:'tencent',subtype:'',confidence:90,evidence:'tencent captcha',cls:rootCls};

        // ── KeyCAPTCHA ────────────────────────────────────────
        if (hasURL('keycaptcha.com')||has('keycaptcha')||countEls('[id*="keycaptcha"]')>0)
            return {type:'keycaptcha',version:'',engine:'keycaptcha',subtype:'',confidence:90,evidence:'keycaptcha',cls:rootCls};

        // ── Lemin CAPTCHA ─────────────────────────────────────
        if (hasURL('lemin.io')||has('lemin-captcha')||has('lemin_captcha_div')||countEls('#lemin-captcha')>0)
            return {type:'lemin',version:'',engine:'lemin',subtype:'',confidence:90,evidence:'lemin captcha',cls:rootCls};

        // ── CyberSiARA ────────────────────────────────────────
        if (hasURL('cybersiara')||has('cybersiara')||countEls('[id*="cybersiara"]')>0)
            return {type:'cybersiara',version:'',engine:'cybersiara',subtype:'',confidence:88,evidence:'cybersiara',cls:rootCls};

        // ── Cutcaptcha ────────────────────────────────────────
        if (hasURL('cutcaptcha')||has('cutcaptcha')||has('mistery_box'))
            return {type:'cutcaptcha',version:'',engine:'cutcaptcha',subtype:'',confidence:88,evidence:'cutcaptcha',cls:rootCls};

        // ── Friendly Captcha ──────────────────────────────────
        if (hasURL('friendlycaptcha')||has('frc-captcha')||has('friendly-challenge'))
            return {type:'friendly_captcha',version:'',engine:'friendly',subtype:'',confidence:90,evidence:'friendly captcha',cls:rootCls};

        // ── atbCAPTCHA ────────────────────────────────────────
        if (hasURL('atb-captcha')||has('atb_captcha')||countEls('[id*="atb-captcha"]')>0)
            return {type:'atb_captcha',version:'',engine:'atb',subtype:'',confidence:88,evidence:'atbcaptcha',cls:rootCls};

        // ── Prosopo Procaptcha ────────────────────────────────
        if (has('procaptcha')||hasURL('prosopo.io')||has('prosopo'))
            return {type:'prosopo',version:'',engine:'prosopo',subtype:'',confidence:88,evidence:'prosopo procaptcha',cls:rootCls};

        // ── CaptchaFox ────────────────────────────────────────
        if (has('captchafox')||hasURL('captchafox.com'))
            return {type:'captchafox',version:'',engine:'captchafox',subtype:'',confidence:88,evidence:'captchafox',cls:rootCls};

        // ── VK Captcha ────────────────────────────────────────
        if (hasURL('vk.com/captcha')||has('vk-captcha')||has('VKWebAppCallAPIMethod'))
            return {type:'vkcaptcha',version:'',engine:'vk',subtype:'',confidence:88,evidence:'vk captcha',cls:rootCls};

        // ── Temu Captcha ──────────────────────────────────────
        if (hasURL('temu.com')&&(has('captcha')||has('verify')))
            return {type:'temu',version:'',engine:'temu',subtype:'',confidence:80,evidence:'temu captcha',cls:rootCls};

        // ── Altcha ────────────────────────────────────────────
        if (has('altcha')||hasURL('altcha.org')||countEls('altcha-widget')>0)
            return {type:'altcha',version:'',engine:'altcha',subtype:'',confidence:85,evidence:'altcha',cls:rootCls};

        // ── Yandex Smart CAPTCHA ──────────────────────────────
        if (hasURL('yandex')||(has('smart-token')||countEls('[id*="yandex-smart"]')>0))
            return {type:'yandex_smart',version:'',engine:'yandex',subtype:'',confidence:80,evidence:'yandex smart',cls:rootCls};

        // ── Audio CAPTCHA ─────────────────────────────────────
        if (countEls('audio')>0||(has('audio')&&has('captcha'))||(has('nghe')&&has('mã')))
            return {type:'audio',version:'',engine:'unknown',subtype:'audio',confidence:75,evidence:'audio element found',cls:rootCls};

        // ── Rotate ────────────────────────────────────────────
        if (has('rotate')||has('rotation')||(bodyText.includes('xoay')&&has('captcha')))
            return {type:'rotate',version:'',engine:'unknown',subtype:'rotate',confidence:72,evidence:'rotate keyword',cls:rootCls};

        // ── Text CAPTCHA (nhập mã) ────────────────────────────
        var hasTextInput = countEls('input[type="text"][placeholder*="captcha"]')>0||
                           countEls('input[id*="captcha"]')>0||
                           countEls('input[name*="captcha"]')>0;
        if (hasTextInput||rootHTML.includes('nhập mã')||rootHTML.includes('enter code')||rootHTML.includes('enter captcha'))
            return {type:'text_captcha',version:'',engine:'unknown',subtype:'text',confidence:85,evidence:'text input + captcha',cls:rootCls};

        // ── Bounding Box ──────────────────────────────────────
        if (has('bounding')||has('bbox')||(bodyText.includes('hãy khoanh vùng')||bodyText.includes('draw box')))
            return {type:'bounding_box',version:'',engine:'unknown',subtype:'bbox',confidence:72,evidence:'bounding box keyword',cls:rootCls};

        // ── Draw Around ───────────────────────────────────────
        if (has('draw-around')||(bodyText.includes('vẽ xung quanh')||bodyText.includes('draw around')))
            return {type:'draw_around',version:'',engine:'unknown',subtype:'draw',confidence:70,evidence:'draw around keyword',cls:rootCls};

        // ── Grid ảnh chung ────────────────────────────────────
        var gridSelectors = ['[class*="item"]','[class*="tile"]','[class*="cell"]','[class*="grid"]','img','canvas'];
        var maxGrid = 0;
        gridSelectors.forEach(function(s){try{var n=(rootEl||document).querySelectorAll(s).length;if(n>maxGrid)maxGrid=n;}catch(e){}});
        if (maxGrid>=4)
            return {type:'grid',version:'',engine:'unknown',subtype:'grid',confidence:70,evidence:'grid count='+maxGrid,cls:rootCls};

        // ── Click order chung ─────────────────────────────────
        var clickKW = ['click in order','select in order','click the','theo th\\u1ee9 t\\u1ef1','order','sequence'];
        for (var k=0;k<clickKW.length;k++){
            if (rootHTML.includes(clickKW[k])||bodyText.includes(clickKW[k]))
                return {type:'click_order',version:'',engine:'unknown',subtype:'click',confidence:72,evidence:'click-order:'+clickKW[k],cls:rootCls};
        }

        // ── Slide chung ───────────────────────────────────────
        if (has('slide')||has('drag')||has('slider')||bodyText.includes('kéo'))
            return {type:'slide',version:'',engine:'unknown',subtype:'slide',confidence:65,evidence:'slide/drag keyword',cls:rootCls};

        // ── Normal image captcha (fallback) ───────────────────
        if (countEls('img')>0 && (hasTextInput||countEls('input[type="text"]')>0))
            return {type:'normal_captcha',version:'',engine:'unknown',subtype:'image',confidence:60,evidence:'image + text input',cls:rootCls};

        return {type:'unknown',version:'',engine:'unknown',subtype:'',confidence:0,evidence:'no pattern matched',cls:rootCls};
    """,
        rect["x"] if rect else None,
        rect["y"] if rect else None,
        rect["w"] if rect else 0,
        rect["h"] if rect else 0)

    if not info:
        info = {"type":"unknown","version":"","engine":"unknown",
                "subtype":"","confidence":0,"evidence":"js null","cls":""}

    logger.info(
        f"[captcha] ── Loại captcha phát hiện ──────────────\n"
        f"  type      : {info.get('type')}\n"
        f"  engine    : {info.get('engine')}  version: {info.get('version')}\n"
        f"  subtype   : {info.get('subtype')}  confidence: {info.get('confidence')}%\n"
        f"  evidence  : {info.get('evidence')}\n"
        f"────────────────────────────────────────────────────"
    )
    return info


def _detect_type_from_dom(driver, rect: dict) -> str:
    info  = check_captcha_type(driver, rect)
    return info.get("type", "unknown")


# ═══════════════════════════════════════════════════════════
# TRÍCH XUẤT THÔNG SỐ CAPTCHA TỪ TRANG
# ═══════════════════════════════════════════════════════════
def _extract_captcha_params(driver) -> dict:
    """
    Trích xuất sitekey / captcha_id / public_key… từ DOM, attribute, script inline.
    Trả về dict chứa tất cả thông số cần thiết.
    """
    params = driver.execute_script("""
        var result = {};
        var html = document.documentElement.innerHTML;

        // ── reCAPTCHA sitekey ────────────────────────────────
        var rcEl = document.querySelector('.g-recaptcha,[data-sitekey]');
        if (rcEl) result.recaptcha_sitekey = rcEl.getAttribute('data-sitekey')||'';
        if (!result.recaptcha_sitekey) {
            var m = html.match(/['"]sitekey['"]\s*:\s*['"]([^'"]{20,})['"]/);
            if (m) result.recaptcha_sitekey = m[1];
        }
        if (!result.recaptcha_sitekey) {
            var m2 = html.match(/data-sitekey=['"]([^'"]{20,})['"]/);
            if (m2) result.recaptcha_sitekey = m2[1];
        }
        var isV3 = html.includes('grecaptcha.execute') || html.includes("'v3'") || html.includes('"v3"');
        result.recaptcha_v3 = isV3;
        var actionM = html.match(/action['"]\s*:\s*['"](\w+)['"]/);
        result.recaptcha_action = actionM ? actionM[1] : 'verify';

        // ── hCaptcha sitekey ──────────────────────────────────
        var hcEl = document.querySelector('.h-captcha,[data-hcaptcha-sitekey]');
        if (hcEl) result.hcaptcha_sitekey = hcEl.getAttribute('data-sitekey')||hcEl.getAttribute('data-hcaptcha-sitekey')||'';
        if (!result.hcaptcha_sitekey) {
            var hm = html.match(/hcaptcha.*?sitekey['"]\s*[=:]\s*['"]([^'"]{20,})['"]/i);
            if (hm) result.hcaptcha_sitekey = hm[1];
        }

        // ── FunCaptcha / Arkose public key ───────────────────
        var fcM = html.match(/public.?key['"]\s*:\s*['"]([A-F0-9-]{30,})['"]/i)
                  || html.match(/data-pkey=['"]([A-F0-9-]{30,})['"]/i);
        result.funcaptcha_key = fcM ? fcM[1] : '';
        var fcSvcM = html.match(/api\.funcaptcha\.com|arkoselabs\.com\/[^\s'"]+/i);
        result.funcaptcha_surl = fcSvcM ? fcSvcM[0] : '';

        // ── Turnstile sitekey ─────────────────────────────────
        var tsEl = document.querySelector('.cf-turnstile,[data-sitekey]');
        if (tsEl && (tsEl.className||'').toLowerCase().includes('turnstile'))
            result.turnstile_sitekey = tsEl.getAttribute('data-sitekey')||'';
        if (!result.turnstile_sitekey) {
            var tsM = html.match(/cf-turnstile.*?data-sitekey=['"]([^'"]+)['"]/i);
            if (tsM) result.turnstile_sitekey = tsM[1];
        }

        // ── GeeTest v3 params ─────────────────────────────────
        var gtM = html.match(/gt['"]\s*:\s*['"]([a-f0-9]{32})['"]/);
        result.geetest_gt = gtM ? gtM[1] : '';
        var chM = html.match(/challenge['"]\s*:\s*['"]([a-f0-9]{32,})['"]/);
        result.geetest_challenge = chM ? chM[1] : '';
        var gapiM = html.match(/api_server['"]\s*:\s*['"]([^'"]+)['"]/);
        result.geetest_api_server = gapiM ? gapiM[1] : '';

        // ── Tencent app_id ────────────────────────────────────
        var tcM = html.match(/app.?id['"]\s*:\s*['"]?(\d{7,10})['"]?/i);
        result.tencent_app_id = tcM ? tcM[1] : '';

        // ── MTCaptcha sitekey ─────────────────────────────────
        var mtEl = document.querySelector('#mtcaptcha,[class*="mtcaptcha"]');
        var mtM = html.match(/sitekey['"]\s*:\s*['"]([A-Za-z0-9_-]{20,})['"]/);
        result.mt_sitekey = mtM ? mtM[1] : '';

        // ── Lemin captcha_id + div_id ─────────────────────────
        var lmM = html.match(/captcha.?id['"]\s*:\s*['"]([A-Za-z0-9_-]+)['"]/i);
        result.lemin_captcha_id = lmM ? lmM[1] : '';
        var lmDiv = document.querySelector('[id*="lemin"]');
        result.lemin_div_id = lmDiv ? lmDiv.id : '';

        // ── CyberSiARA master_url_id ──────────────────────────
        var csM = html.match(/master.?url.?id['"]\s*:\s*['"]([^'"]+)['"]/i);
        result.cybersiara_master_id = csM ? csM[1] : '';

        // ── Amazon WAF ────────────────────────────────────────
        var awsM = html.match(/aws.?waf.?token['"]\s*:\s*['"]([^'"]+)['"]/i);
        result.amazon_waf_token = awsM ? awsM[1] : '';
        var awsSK = html.match(/awswaf.*?siteKey['"]\s*:\s*['"]([^'"]+)['"]/i);
        result.amazon_waf_sitekey = awsSK ? awsSK[1] : '';

        // ── CaptchaFox sitekey ────────────────────────────────
        var cfxM = html.match(/captchafox.*?sitekey['"]\s*[=:]\s*['"]([^'"]+)['"]/i);
        result.captchafox_sitekey = cfxM ? cfxM[1] : '';

        // ── Prosopo sitekey ───────────────────────────────────
        var proM = html.match(/procaptcha.*?sitekey['"]\s*[=:]\s*['"]([^'"]+)['"]/i);
        result.prosopo_sitekey = proM ? proM[1] : '';

        // ── Friendly Captcha sitekey ──────────────────────────
        var fcapEl = document.querySelector('.frc-captcha,[data-sitekey]');
        if (fcapEl && (fcapEl.className||'').includes('frc'))
            result.friendly_sitekey = fcapEl.getAttribute('data-sitekey')||'';

        return result;
    """) or {}

    logger.info(f"[captcha] Thông số trang: { {k:v for k,v in params.items() if v} }")
    return params


# ═══════════════════════════════════════════════════════════
# CURL_CFFI SUBMIT (TLS FINGERPRINT BYPASS)
# ═══════════════════════════════════════════════════════════
def _parse_token_dict(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict) and "code" in raw:
        data = raw["code"]
        if isinstance(data, str):
            parsed = {}
            for part in data.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    parsed[k.strip()] = v.strip()
            return parsed if parsed else raw
        if isinstance(data, dict):
            return data
    return raw if isinstance(raw, dict) else {}


def submit_register_curl_cffi(driver, token_result, form_data: dict,
                               progress_cb=None) -> dict:
    """Submit form đăng ký trực tiếp qua HTTP (curl_cffi Chrome120 impersonate)."""
    try:
        from curl_cffi import requests as curl_req
    except ImportError:
        logger.error("[curl_cffi] Chưa cài — pip install curl_cffi")
        return {"success": False, "status": 0, "response": "curl_cffi not installed"}

    if progress_cb:
        progress_cb("🕵️ Đang submit qua curl_cffi (bypass TLS fingerprint)...")

    selenium_cookies = {}
    try:
        for c in driver.get_cookies():
            selenium_cookies[c["name"]] = c["value"]
        logger.info(f"[curl_cffi] {len(selenium_cookies)} cookies từ Selenium")
    except Exception as e:
        logger.warning(f"[curl_cffi] Lấy cookies thất bại: {e}")

    token = _parse_token_dict(token_result)
    payload = {
        "username":       form_data.get("user", ""),
        "password":       form_data.get("password", ""),
        "phone":          form_data.get("phone", ""),
        "fullname":       form_data.get("full_name", "").upper(),
        "captcha_id":     GEETEST_V4_ID,
        "lot_number":     token.get("lot_number", ""),
        "pass_token":     token.get("pass_token", ""),
        "gen_time":       token.get("gen_time", ""),
        "captcha_output": token.get("captcha_output", ""),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept":       "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer":      REGISTER_URL,
        "Origin":       BASE_URL,
    }
    api_url = form_data.get("api_url") or REGISTER_API_URL
    logger.info(f"[curl_cffi] POST → {api_url} | user={payload['username']}")
    try:
        session = curl_req.Session()
        for name, value in selenium_cookies.items():
            session.cookies.set(name, value)
        resp = session.post(api_url, json=payload, headers=headers,
                            impersonate="chrome120", timeout=30)
        body = resp.text[:500]
        logger.info(f"[curl_cffi] {resp.status_code}: {body}")
        body_lower = body.lower()
        success = resp.status_code in (200, 201) and any(
            kw in body_lower for kw in ('"success":true', '"code":0', '"status":1', '"ok":true', '"result":1')
        )
        if progress_cb:
            progress_cb(f"{'✅' if success else '⚠️'} curl_cffi: HTTP {resp.status_code} | {body[:80]}")
        return {"success": success, "status": resp.status_code, "response": body}
    except Exception as e:
        logger.error(f"[curl_cffi] Lỗi: {e}")
        if progress_cb:
            progress_cb(f"❌ curl_cffi lỗi: {e}")
        return {"success": False, "status": 0, "response": str(e)}


# ═══════════════════════════════════════════════════════════
# TOKEN-BASED SOLVERS
# ═══════════════════════════════════════════════════════════

def _solve_geetest_v4_token(website_url: str) -> dict | None:
    if not solver:
        return None
    logger.info(f"[captcha] GeeTest v4 token — id={GEETEST_V4_ID}")
    try:
        result = solver.geetest_v4(captcha_id=GEETEST_V4_ID, url=website_url)
        logger.info(f"[captcha] GeeTest v4 token OK: {str(result)[:120]}")
        return result
    except Exception as e:
        logger.error(f"[captcha] GeeTest v4 lỗi: {e}")
        return None


def _solve_geetest_v3_token(website_url: str, gt: str, challenge: str,
                             api_server: str = "") -> dict | None:
    if not solver or not gt or not challenge:
        return None
    logger.info(f"[captcha] GeeTest v3 token — gt={gt[:8]}...")
    try:
        kwargs = dict(gt=gt, challenge=challenge, url=website_url)
        if api_server:
            kwargs["apiServer"] = api_server   # SDK rename_params: apiServer → api_server
        result = solver.geetest(**kwargs)
        logger.info(f"[captcha] GeeTest v3 token OK: {str(result)[:120]}")
        return result
    except Exception as e:
        logger.error(f"[captcha] GeeTest v3 lỗi: {e}")
        return None


def _solve_recaptcha_token(website_url: str, sitekey: str,
                            version: str = "v2", enterprise: bool = False,
                            action: str = "verify", score: float = 0.7) -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] reCAPTCHA {version}{'(enterprise)' if enterprise else ''} — key={sitekey[:12]}...")
    try:
        kwargs = dict(sitekey=sitekey, url=website_url)
        if version == "v3":
            kwargs["version"] = "v3"
            kwargs["action"] = action
            kwargs["score"] = score
        if enterprise:
            kwargs["enterprise"] = 1
        result = solver.recaptcha(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] reCAPTCHA token OK: {str(token)[:40]}...")
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] reCAPTCHA lỗi: {e}")
        return None


def _solve_hcaptcha_token(website_url: str, sitekey: str) -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] hCaptcha — key={sitekey[:12]}...")
    try:
        result = solver.hcaptcha(sitekey=sitekey, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] hCaptcha token OK: {str(token)[:40]}...")
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] hCaptcha lỗi: {e}")
        return None


def _solve_funcaptcha_token(website_url: str, public_key: str,
                             service_url: str = "") -> str | None:
    if not solver or not public_key:
        return None
    logger.info(f"[captcha] FunCaptcha — key={public_key[:12]}...")
    try:
        kwargs = dict(public_key=public_key, url=website_url)
        if service_url:
            kwargs["serviceUrl"] = service_url
        result = solver.funcaptcha(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] FunCaptcha token OK")
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] FunCaptcha lỗi: {e}")
        return None


def _solve_turnstile_token(website_url: str, sitekey: str,
                            action: str = "") -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] Turnstile — key={sitekey[:12]}...")
    try:
        kwargs = dict(sitekey=sitekey, url=website_url)
        if action:
            kwargs["action"] = action
        result = solver.turnstile(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] Turnstile token OK")
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] Turnstile lỗi: {e}")
        return None


def _solve_capy_token(website_url: str, sitekey: str) -> dict | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] Capy — key={sitekey[:12]}...")
    try:
        result = solver.capy(sitekey=sitekey, url=website_url)
        logger.info(f"[captcha] Capy OK: {str(result)[:80]}")
        return result if isinstance(result, dict) else {"code": result}
    except Exception as e:
        logger.error(f"[captcha] Capy lỗi: {e}")
        return None


def _solve_amazon_waf_token(website_url: str, sitekey: str,
                             iv: str = "", context: str = "") -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] Amazon WAF — key={sitekey[:12]}...")
    try:
        kwargs = dict(sitekey=sitekey, url=website_url)
        if iv:
            kwargs["iv"] = iv
        if context:
            kwargs["context"] = context
        result = solver.amazon_waf(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] Amazon WAF token OK")
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] Amazon WAF lỗi: {e}")
        return None


def _solve_tencent_token(website_url: str, app_id: str) -> dict | None:
    if not solver or not app_id:
        return None
    logger.info(f"[captcha] Tencent — app_id={app_id}")
    try:
        result = solver.tencent(app_id=app_id, url=website_url)
        logger.info(f"[captcha] Tencent OK")
        return result if isinstance(result, dict) else {"code": result}
    except Exception as e:
        logger.error(f"[captcha] Tencent lỗi: {e}")
        return None


def _solve_mtcaptcha_token(website_url: str, sitekey: str) -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] MTCaptcha — key={sitekey[:12]}...")
    try:
        result = solver.mtcaptcha(sitekey=sitekey, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] MTCaptcha lỗi: {e}")
        return None


def _solve_lemin_token(website_url: str, captcha_id: str,
                        div_id: str, api_server: str = "") -> dict | None:
    if not solver or not captcha_id:
        return None
    logger.info(f"[captcha] Lemin — captcha_id={captcha_id}")
    try:
        kwargs = dict(captcha_id=captcha_id, div_id=div_id or "lemin-captcha", url=website_url)
        if api_server:
            kwargs["api_server"] = api_server
        result = solver.lemin(**kwargs)
        return result if isinstance(result, dict) else {"code": result}
    except Exception as e:
        logger.error(f"[captcha] Lemin lỗi: {e}")
        return None


def _solve_cybersiara_token(website_url: str, master_id: str) -> str | None:
    if not solver or not master_id:
        return None
    logger.info(f"[captcha] CyberSiARA — master_id={master_id[:12]}...")
    try:
        result = solver.cybersiara(master_url_id=master_id, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] CyberSiARA lỗi: {e}")
        return None


def _solve_cutcaptcha_token(website_url: str, mistery_box: str,
                             api_key: str) -> str | None:
    if not solver or not mistery_box:
        return None
    logger.info(f"[captcha] Cutcaptcha")
    try:
        result = solver.cutcaptcha(mistery_box=mistery_box,
                                   api_key=api_key, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] Cutcaptcha lỗi: {e}")
        return None


def _solve_friendly_captcha_token(website_url: str, sitekey: str,
                                   api_server: str = "") -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] Friendly Captcha — key={sitekey[:12]}...")
    try:
        kwargs = dict(sitekey=sitekey, url=website_url)
        if api_server:
            kwargs["api_server"] = api_server
        result = solver.friendly_captcha(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] Friendly Captcha lỗi: {e}")
        return None


def _solve_datadome_token(website_url: str, captcha_url: str,
                           proxy: str = "") -> str | None:
    if not solver or not captcha_url:
        return None
    logger.info(f"[captcha] DataDome — captcha_url={captcha_url[:40]}...")
    try:
        kwargs = dict(captcha_url=captcha_url, url=website_url)
        if proxy:
            kwargs["proxy"] = {"type": "http", "uri": proxy}
        result = solver.datadome(**kwargs)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] DataDome lỗi: {e}")
        return None


def _solve_captchafox_token(website_url: str, sitekey: str) -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] CaptchaFox — key={sitekey[:12]}...")
    try:
        result = solver.captchafox(sitekey=sitekey, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] CaptchaFox lỗi: {e}")
        return None


def _solve_prosopo_token(website_url: str, sitekey: str) -> str | None:
    if not solver or not sitekey:
        return None
    logger.info(f"[captcha] Prosopo — key={sitekey[:12]}...")
    try:
        result = solver.prosopo(sitekey=sitekey, url=website_url)
        token = result.get("code", result) if isinstance(result, dict) else result
        return str(token)
    except Exception as e:
        logger.error(f"[captcha] Prosopo lỗi: {e}")
        return None


def _solve_vkcaptcha_token(website_url: str) -> str | None:
    if not solver:
        return None
    try:
        result = solver.vkcaptcha(url=website_url)
        return str(result.get("code", result) if isinstance(result, dict) else result)
    except Exception as e:
        logger.error(f"[captcha] VK Captcha lỗi: {e}")
        return None


def _solve_temu_token(website_url: str) -> str | None:
    if not solver:
        return None
    try:
        result = solver.temu(url=website_url)
        return str(result.get("code", result) if isinstance(result, dict) else result)
    except Exception as e:
        logger.error(f"[captcha] Temu Captcha lỗi: {e}")
        return None


def _solve_altcha_token(website_url: str) -> str | None:
    if not solver:
        return None
    try:
        result = solver.altcha(url=website_url)
        return str(result.get("code", result) if isinstance(result, dict) else result)
    except Exception as e:
        logger.error(f"[captcha] Altcha lỗi: {e}")
        return None


def _solve_yandex_smart_token(website_url: str, sitekey: str = "") -> str | None:
    if not solver:
        return None
    try:
        kwargs = dict(url=website_url)
        if sitekey:
            kwargs["sitekey"] = sitekey
        result = solver.yandex_smart(**kwargs)
        return str(result.get("code", result) if isinstance(result, dict) else result)
    except Exception as e:
        logger.error(f"[captcha] Yandex Smart lỗi: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# TOKEN INJECTORS
# ═══════════════════════════════════════════════════════════

def _inject_geetest_v4_token(driver, token_result) -> bool:
    if not token_result:
        return False
    tok = _parse_token_dict(token_result)
    lot_number     = tok.get("lot_number", "")
    pass_token     = tok.get("pass_token", "")
    gen_time       = tok.get("gen_time", "")
    captcha_output = tok.get("captcha_output", "")
    sign_token     = tok.get("sign_token", "")
    captcha_id     = tok.get("captcha_id", GEETEST_V4_ID)

    logger.info(f"[captcha] Inject GeeTest v4: lot={lot_number[:8]}...")
    injected = driver.execute_script("""
        var L=arguments[0],P=arguments[1],G=arguments[2],C=arguments[3],S=arguments[4],ID=arguments[5];
        var submitted=false;
        if(window.handlerGeetest4&&typeof window.handlerGeetest4==='function'){
            window.handlerGeetest4({lot_number:L,pass_token:P,gen_time:G,captcha_output:C,sign_token:S,captcha_id:ID});
            submitted=true;
        }
        document.dispatchEvent(new CustomEvent('geetest4:success',{bubbles:true,detail:{lot_number:L,pass_token:P,gen_time:G,captcha_output:C,sign_token:S,captcha_id:ID}}));
        var fm={'lot_number':L,'pass_token':P,'gen_time':G,'captcha_output':C,'sign_token':S,'captcha_id':ID,'geetest_lotNumber':L,'geetest_passToken':P,'geetest_genTime':G,'geetest_captchaOutput':C};
        Object.keys(fm).forEach(function(n){
            ['input[name="'+n+'"]','#'+n,'[data-name="'+n+'"]'].forEach(function(sel){
                var el=document.querySelector(sel);
                if(el){el.value=fm[n];el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
            });
        });
        if(window.__gt4_cb&&typeof window.__gt4_cb==='function'){window.__gt4_cb({lot_number:L,pass_token:P,gen_time:G,captcha_output:C});submitted=true;}
        return submitted?'callback':'fields';
    """, lot_number, pass_token, gen_time, captcha_output, sign_token, captcha_id)

    logger.info(f"[captcha] GeeTest v4 inject: {injected}")
    _human_sleep(1.5, 2.5)
    still = _find_captcha_rect(driver, timeout=2)
    if still is None:
        logger.info("[captcha] GeeTest v4 inject ✅")
        return True
    _press_ok(driver, still)
    _human_sleep(1.0, 1.5)
    return _find_captcha_rect(driver, timeout=2) is None


def _inject_recaptcha_token(driver, token: str) -> bool:
    if not token:
        return False
    logger.info("[captcha] Inject reCAPTCHA token...")
    driver.execute_script("""
        var t = arguments[0];
        // Set response textarea (v2/v3)
        var areas = document.querySelectorAll('[name="g-recaptcha-response"]');
        areas.forEach(function(el){el.innerHTML=t;el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
        // Try grecaptcha.execute callback
        if(window.___grecaptcha_cfg&&window.___grecaptcha_cfg.clients){
            Object.values(window.___grecaptcha_cfg.clients).forEach(function(c){
                try{
                    Object.values(c).forEach(function(v){
                        if(v&&typeof v.callback==='function') v.callback(t);
                    });
                }catch(e){}
            });
        }
        // Dispatch event
        document.dispatchEvent(new CustomEvent('captcha-solved',{detail:{token:t}}));
        // Set hidden inputs
        var inputs=document.querySelectorAll('input[name*="recaptcha"],input[id*="recaptcha"]');
        inputs.forEach(function(el){el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
    """, token)
    _human_sleep(0.5, 1.0)
    return True


def _inject_hcaptcha_token(driver, token: str) -> bool:
    if not token:
        return False
    logger.info("[captcha] Inject hCaptcha token...")
    driver.execute_script("""
        var t = arguments[0];
        var areas = document.querySelectorAll('[name="h-captcha-response"],[id*="h-captcha-response"]');
        areas.forEach(function(el){el.innerHTML=t;el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
        var inputs = document.querySelectorAll('input[name*="hcaptcha"]');
        inputs.forEach(function(el){el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
        document.dispatchEvent(new CustomEvent('hcaptcha:success',{detail:{token:t}}));
    """, token)
    _human_sleep(0.5, 1.0)
    return True


def _inject_geetest_v3_token(driver, token_result) -> bool:
    """
    Inject GeeTest v3 token vào trang.
    2captcha trả về: {"code": "challenge=xxx;validate=yyy;seccode=zzz"}
    Cần inject đủ 3 field riêng biệt vào DOM.
    """
    if not token_result:
        return False
    tok = _parse_token_dict(token_result)
    # code có thể là string "challenge=xxx;validate=yyy;seccode=zzz"
    # sau _parse_token_dict → {"challenge":"xxx","validate":"yyy","seccode":"zzz"}
    challenge = tok.get("challenge", "")
    validate  = tok.get("validate",  "")
    seccode   = tok.get("seccode",   "")

    # Fallback: nếu parse không ra được 3 field riêng
    if not validate and "code" in (token_result if isinstance(token_result, dict) else {}):
        raw = token_result["code"]
        if isinstance(raw, str):
            for part in raw.split(";"):
                if "challenge=" in part:
                    challenge = part.split("=", 1)[1]
                elif "validate=" in part:
                    validate  = part.split("=", 1)[1]
                elif "seccode=" in part:
                    seccode   = part.split("=", 1)[1]

    logger.info(f"[captcha] Inject GeeTest v3: validate={validate[:16]}...")

    driver.execute_script("""
        var challenge=arguments[0], validate=arguments[1], seccode=arguments[2];

        // Cách 1: set hidden input fields (cách phổ biến nhất)
        var fieldMap = {
            'geetest_challenge': challenge,
            'geetest_validate':  validate,
            'geetest_seccode':   seccode,
            'challenge':         challenge,
            'validate':          validate,
            'seccode':           seccode,
        };
        Object.keys(fieldMap).forEach(function(name) {
            var val = fieldMap[name];
            ['input[name="'+name+'"]','#'+name,'[data-name="'+name+'"]','textarea[name="'+name+'"]'].forEach(function(sel){
                var el = document.querySelector(sel);
                if (el) {
                    el.value = val;
                    el.dispatchEvent(new Event('input',  {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }
            });
        });

        // Cách 2: gọi callback nếu trang dùng pattern initGeetest(data, fn)
        if (window._gt_callback && typeof window._gt_callback === 'function') {
            window._gt_callback({
                geetest_challenge: challenge,
                geetest_validate:  validate,
                geetest_seccode:   seccode,
            });
        }

        // Cách 3: dispatch event để Vue/React components lắng nghe
        document.dispatchEvent(new CustomEvent('geetest:success', {
            bubbles: true,
            detail: { challenge: challenge, validate: validate, seccode: seccode }
        }));

        // Cách 4: tìm form và submit luôn nếu có đủ field
        var form = document.querySelector('form');
        if (form) {
            ['geetest_challenge','geetest_validate','geetest_seccode'].forEach(function(n, i) {
                var vals = [challenge, validate, seccode];
                var el = form.querySelector('[name="'+n+'"]');
                if (!el) {
                    el = document.createElement('input');
                    el.type = 'hidden'; el.name = n;
                    form.appendChild(el);
                }
                el.value = vals[i];
            });
        }
    """, challenge, validate, seccode)

    _human_sleep(0.8, 1.5)
    still = _find_captcha_rect(driver, timeout=2)
    success = still is None
    logger.info(f"[captcha] GeeTest v3 inject: {'✅' if success else '⚠️ vẫn còn captcha'}")
    return success


def _inject_funcaptcha_token(driver, token: str) -> bool:
    if not token:
        return False
    logger.info("[captcha] Inject FunCaptcha token...")
    driver.execute_script("""
        var t = arguments[0];
        var inputs = document.querySelectorAll('input[id*="FunCaptcha"],input[name*="fc-token"],input[name*="arkose"]');
        inputs.forEach(function(el){el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
        if(window.ArkoseEnforcement&&window.ArkoseEnforcement.solved) window.ArkoseEnforcement.solved(t);
        document.dispatchEvent(new CustomEvent('arkose:solved',{detail:{token:t}}));
    """, token)
    _human_sleep(0.5, 1.0)
    return True


def _inject_turnstile_token(driver, token: str) -> bool:
    if not token:
        return False
    logger.info("[captcha] Inject Turnstile token...")
    driver.execute_script("""
        var t = arguments[0];
        var inputs = document.querySelectorAll('[name="cf-turnstile-response"],[id*="turnstile"]');
        inputs.forEach(function(el){el.value=t;el.dispatchEvent(new Event('change',{bubbles:true}));});
        if(window.turnstile&&window.turnstile.getResponse) window.turnstile._callbacks&&Object.values(window.turnstile._callbacks).forEach(function(cb){try{cb(t);}catch(e){}});
        document.dispatchEvent(new CustomEvent('turnstile-solved',{detail:{token:t}}));
    """, token)
    _human_sleep(0.5, 1.0)
    return True


def _inject_generic_token(driver, token: str, field_names: list = None) -> bool:
    """Inject token vào bất kỳ captcha nào qua hidden input hoặc textarea."""
    if not token:
        return False
    names = field_names or ["captcha", "captcha_token", "captcha-response", "captcha_response"]
    driver.execute_script("""
        var t = arguments[0]; var names = arguments[1];
        names.forEach(function(n){
            ['input[name="'+n+'"]','textarea[name="'+n+'"]','#'+n,'[data-name="'+n+'"]'].forEach(function(sel){
                var el=document.querySelector(sel);
                if(el){el.value=t;el.innerHTML=t;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
            });
        });
        document.dispatchEvent(new CustomEvent('captcha-solved',{detail:{token:t,response:t}}));
    """, token, names)
    _human_sleep(0.3, 0.8)
    return True


# ═══════════════════════════════════════════════════════════
# IMAGE-BASED SOLVERS
# ═══════════════════════════════════════════════════════════

def _solve_as_normal(img_path: str, instructions: str = "") -> str | None:
    """Normal image CAPTCHA → text answer."""
    if not solver:
        return None
    logger.info("[captcha] Gửi 2captcha (normal image)...")
    try:
        kwargs = dict(file=img_path)
        if instructions:
            kwargs["textinstructions"] = instructions
        result = solver.normal(**kwargs)
        code = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] Normal captcha answer: {code}")
        return str(code)
    except Exception as e:
        logger.error(f"[captcha] Normal image lỗi: {e}")
        return None


def _solve_as_text(img_path: str, instructions: str = "") -> str | None:
    """Text CAPTCHA → text answer."""
    if not solver:
        return None
    logger.info("[captcha] Gửi 2captcha (text captcha)...")
    try:
        result = solver.text(textcaptcha=instructions or "What does this text say?")
        code = result.get("code", result) if isinstance(result, dict) else result
        return str(code)
    except Exception as e:
        logger.error(f"[captcha] Text captcha lỗi: {e}")
        return _solve_as_normal(img_path, instructions)


def _solve_as_coordinates(img_path: str) -> list:
    """Gửi ảnh → worker click đúng vị trí → trả tọa độ [[x,y],...]"""
    if not solver:
        return []
    hint = (
        "Click vào các biểu tượng/hình ảnh theo đúng thứ tự số hiển thị ở hàng trên. "
        "Nếu là lưới ô hình ảnh, nhấp vào TẤT CẢ ô phù hợp với yêu cầu."
    )
    logger.info("[captcha] Gửi 2captcha (coordinates)...")
    try:
        result = solver.coordinates(file=img_path, textinstructions=hint)
        raw = result.get("code", result) if isinstance(result, dict) else result
        pairs = []
        if isinstance(raw, list):
            for p in raw:
                if isinstance(p, dict) and "x" in p and "y" in p:
                    pairs.append([int(p["x"]), int(p["y"])])
        else:
            raw_str = re.sub(r'^coordinates:', '', str(raw), flags=re.IGNORECASE).strip()
            for part in raw_str.split(";"):
                xm = re.search(r'x=(\d+)', part, re.I)
                ym = re.search(r'y=(\d+)', part, re.I)
                if xm and ym:
                    pairs.append([int(xm.group(1)), int(ym.group(1))])
                else:
                    nums = re.findall(r'\d+', part)
                    if len(nums) >= 2:
                        pairs.append([int(nums[0]), int(nums[1])])
        logger.info(f"[captcha] Tọa độ ({len(pairs)}): {pairs}")
        return pairs
    except Exception as e:
        logger.error(f"[captcha] Coordinates lỗi: {e}")
        return []


def _solve_as_grid(img_path: str, rows: int = 3, cols: int = 3) -> list:
    """Grid → list số ô (1-indexed)"""
    if not solver:
        return []
    logger.info(f"[captcha] Gửi 2captcha (grid {rows}x{cols})...")
    try:
        result = solver.grid(img_path, hintText="Chọn tất cả ô phù hợp", rows=rows, cols=cols)
        code = result.get("code", "") if isinstance(result, dict) else str(result)
        code = re.sub(r'^click:', '', code, flags=re.I).strip()
        tiles = [int(x) for x in code.split(",") if x.strip().isdigit()]
        logger.info(f"[captcha] Grid tiles: {tiles}")
        return tiles
    except Exception as e:
        logger.error(f"[captcha] Grid lỗi: {e}")
        return []


def _solve_as_rotate(img_path: str) -> int | None:
    """Rotate → góc quay (degrees)"""
    if not solver:
        return None
    logger.info("[captcha] Gửi 2captcha (rotate)...")
    try:
        result = solver.rotate(file=img_path)
        code = result.get("code", result) if isinstance(result, dict) else result
        angle = int(re.search(r'\d+', str(code)).group())
        logger.info(f"[captcha] Rotate angle: {angle}°")
        return angle
    except Exception as e:
        logger.error(f"[captcha] Rotate lỗi: {e}")
        return None


def _solve_as_bounding_box(img_path: str, hint: str = "") -> list:
    """Bounding box → list của [[x1,y1,x2,y2],...]"""
    if not solver:
        return []
    logger.info("[captcha] Gửi 2captcha (bounding_box)...")
    try:
        result = solver.bounding_box(file=img_path, hintText=hint or "Draw bounding box")
        code = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] BBox result: {code}")
        return code if isinstance(code, list) else []
    except Exception as e:
        logger.error(f"[captcha] BoundingBox lỗi: {e}")
        return []


def _solve_as_draw_around(img_path: str) -> list:
    """Draw Around → list of polygon points"""
    if not solver:
        return []
    logger.info("[captcha] Gửi 2captcha (draw_around)...")
    try:
        result = solver.draw_around(file=img_path)
        code = result.get("code", result) if isinstance(result, dict) else result
        return code if isinstance(code, list) else []
    except Exception as e:
        logger.error(f"[captcha] DrawAround lỗi: {e}")
        return []


def _solve_as_audio(driver) -> str | None:
    """Audio CAPTCHA → download mp3/wav → gửi 2captcha audio solver"""
    if not solver:
        return None
    logger.info("[captcha] Audio captcha — tìm file audio...")
    try:
        audio_src = driver.execute_script("""
            var a = document.querySelector('audio source,audio[src]');
            if (!a) return null;
            return a.src || a.getAttribute('src');
        """)
        if not audio_src:
            logger.warning("[captcha] Không tìm thấy audio src")
            return None
        import urllib.request
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        urllib.request.urlretrieve(audio_src, tmp.name)
        tmp.close()
        result = solver.audio(file=tmp.name, lang="en")
        os.unlink(tmp.name)
        code = result.get("code", result) if isinstance(result, dict) else result
        logger.info(f"[captcha] Audio answer: {code}")
        return str(code)
    except Exception as e:
        logger.error(f"[captcha] Audio lỗi: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# CLICK INTERACTIONS
# ═══════════════════════════════════════════════════════════

def _apply_coordinates(driver, coords: list, rect: dict):
    vp_w = driver.execute_script("return window.innerWidth;") or 1
    dpr  = Image.open(BytesIO(driver.get_screenshot_as_png())).width / vp_w
    _human_sleep(0.4, 0.9)
    for idx, (cx, cy) in enumerate(coords):
        px = int(rect["x"] + cx / dpr) + random.randint(-3, 3)
        py = int(rect["y"] + cy / dpr) + random.randint(-3, 3)
        logger.info(f"  [captcha] Click {idx+1}: ({px},{py})")
        hold = random.randint(80, 180)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "e.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));"
            "e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}", px, py)
        time.sleep(hold / 1000)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){"
            "e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:arguments[0],clientY:arguments[1]}));"
            "}", px, py)
        _human_sleep(0.7, 1.5)
    _human_sleep(0.5, 1.0)


def _apply_grid_tiles(driver, tiles: list, rect: dict, count: int = 9):
    rows = 3 if count > 6 else 2
    cols = 3 if count > 6 else (3 if count > 4 else 2)
    cell_w = rect["w"] / cols
    cell_h = rect["h"] / rows
    for tile in tiles:
        idx = tile - 1
        row = idx // cols
        col = idx % cols
        cx  = rect["x"] + col * cell_w + cell_w / 2 + random.randint(-5, 5)
        cy  = rect["y"] + row * cell_h + cell_h / 2 + random.randint(-5, 5)
        logger.info(f"  [captcha] Grid tile {tile}: ({cx:.0f},{cy:.0f})")
        _human_sleep(0.5, 1.1)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,"
            "clientX:arguments[0],clientY:arguments[1]}));}", cx, cy)
    _human_sleep(0.5, 1.0)


def _apply_rotate(driver, angle: int, rect: dict):
    """Xoay slider captcha theo angle độ."""
    logger.info(f"[captcha] Rotate: kéo {angle}°")
    cx = rect["x"] + rect["w"] / 2
    cy = rect["y"] + rect["h"] * 0.85
    driver.execute_script(
        "var e=document.elementFromPoint(arguments[0],arguments[1]);"
        "if(e){e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));}",
        cx, cy)
    steps = max(5, abs(angle) // 5)
    for i in range(steps):
        nx = cx + (angle / steps) * i * (rect["w"] / 360)
        driver.execute_script(
            "var e=document.elementFromPoint(arguments[0],arguments[1]);"
            "if(e){e.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));}",
            nx, cy)
        time.sleep(0.03)
    driver.execute_script(
        "var e=document.elementFromPoint(arguments[0],arguments[1]);"
        "if(e){e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:arguments[0],clientY:arguments[1]}));}",
        cx + angle * rect["w"] / 360, cy)
    _human_sleep(0.5, 1.0)


def _fill_text_input(driver, text: str, rect: dict):
    """Điền text vào input trong vùng captcha."""
    driver.execute_script("""
        var rect_x=arguments[0],rect_y=arguments[1],rect_w=arguments[2],rect_h=arguments[3],text=arguments[4];
        var inputs=document.querySelectorAll('input[type="text"],input[type="number"]');
        for(var i=0;i<inputs.length;i++){
            var r=inputs[i].getBoundingClientRect();
            if(r.top>=rect_y-50&&r.top<=rect_y+rect_h+50){
                inputs[i].focus();inputs[i].value=text;
                inputs[i].dispatchEvent(new Event('input',{bubbles:true}));
                inputs[i].dispatchEvent(new Event('change',{bubbles:true}));
                break;
            }
        }
    """, rect["x"], rect["y"], rect["w"], rect["h"], text)


def _press_ok(driver, rect: dict = None) -> bool:
    clicked = driver.execute_script("""
        var txts=['OK','ok','Xác nhận','Confirm','确认','Submit','Verify','验证'];
        var els=Array.from(document.querySelectorAll('div,button,span,a'));
        for(var i=els.length-1;i>=0;i--){
            var el=els[i]; var txt=(el.innerText||el.textContent||'').trim();
            if(txts.indexOf(txt)>=0&&el.offsetParent!==null){
                el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
                el.click();
                return el.tagName+'/'+(el.className||'').toString().substring(0,40);
            }
        }
        return null;
    """)
    if clicked:
        logger.info(f"[captcha] OK clicked: {clicked}")
        _human_sleep(1.2, 2.0)
        return True

    for xp in ["//*[normalize-space(text())='OK']",
                "//*[contains(@class,'submit')]",
                "//*[contains(@class,'confirm')]",
                "//*[contains(@class,'verify')]"]:
        try:
            e = driver.find_element(By.XPATH, xp)
            if e.is_displayed():
                e.click()
                logger.info(f"[captcha] OK (XPath): {xp}")
                _human_sleep(1.2, 2.0)
                return True
        except Exception:
            pass

    if rect:
        for ry in [0.74, 0.78, 0.70, 0.82, 0.90]:
            px = rect["x"] + rect["w"] * 0.50
            py = rect["y"] + rect["h"] * ry
            driver.execute_script(
                "var e=document.elementFromPoint(arguments[0],arguments[1]);"
                "if(e){e.dispatchEvent(new MouseEvent('click',{bubbles:true,"
                "clientX:arguments[0],clientY:arguments[1]}));}", px, py)
            _human_sleep(1.0, 1.5)
            if _find_captcha_rect(driver, timeout=1) is None:
                logger.info(f"[captcha] Modal tắt sau click ry={ry}")
                return True

    logger.warning("[captcha] Không tìm được nút OK")
    return False


# ═══════════════════════════════════════════════════════════
# HÀM CHÍNH — GIẢI CAPTCHA
# ═══════════════════════════════════════════════════════════

def solve_captcha_on_page(driver, form_data: dict = None,
                          progress_cb=None) -> bool:
    """
    Giải captcha trên trang hiện tại. Hỗ trợ 30+ loại captcha.

    form_data (tuỳ chọn): {"user", "phone", "password", "full_name", "api_url"}
      → Khi gặp GeeTest v4: thử submit form trực tiếp qua curl_cffi sau khi lấy token.

    progress_cb (tuỳ chọn): callback(str) để gửi tiến trình về Telegram.
    """
    if not solver:
        logger.error("[captcha] solver=None ❌")
        return False

    MAX_ROUNDS   = 8
    solved_count = 0
    current_url  = driver.current_url
    params       = None   # Lazy-load

    for rnd in range(1, MAX_ROUNDS + 1):
        logger.info(f"[captcha] ── Vòng {rnd}/{MAX_ROUNDS} ──────────────────────────")

        rect = _find_captcha_rect(driver, timeout=6)

        if rect is None:
            if solved_count > 0:
                logger.info("[captcha] Không còn captcha → click ĐĂNG KÝ submit...")
                _human_sleep(1.5, 2.5)
                driver.execute_script("""
                    var btns=Array.from(document.querySelectorAll('div,button,span'))
                        .filter(function(e){return (e.innerText||'').trim()==='ĐĂNG KÝ'&&e.offsetParent!==null;});
                    if(btns.length>0){var b=btns[btns.length-1];
                        b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                        b.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));b.click();}
                """)
                _human_sleep(3.0, 4.0)
                solved_count = 0
                continue
            else:
                logger.info("[captcha] Không tìm thấy captcha trên trang")
                break

        ctype = _detect_type_from_dom(driver, rect)
        current_url = driver.current_url

        if progress_cb:
            progress_cb(f"🔍 Phát hiện captcha: `{ctype}` — đang giải...")

        # Lazy-load params khi cần
        if params is None:
            params = _extract_captcha_params(driver)

        # ══════════════════════════════════════════════════════
        # GEETEST V4
        # ══════════════════════════════════════════════════════
        if ctype in ("geetest_v4_slide", "geetest_v4_click", "geetest_v4_grid"):
            if progress_cb:
                progress_cb(f"🔑 Lấy token GeeTest v4 ({ctype}) từ 2captcha...")
            token = _solve_geetest_v4_token(current_url)
            if not token:
                logger.error("[captcha] GeeTest v4 token thất bại ❌")
                break
            if form_data:
                http = submit_register_curl_cffi(driver, token, form_data, progress_cb)
                if http.get("success"):
                    logger.info("[captcha] curl_cffi submit ✅")
                    return True
                if progress_cb:
                    progress_cb("⚠️ curl_cffi chưa được → inject token vào browser...")
            if _inject_geetest_v4_token(driver, token):
                solved_count += 1
                _human_sleep(1.5, 2.5)
                continue
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    coords = _solve_as_coordinates(img_path)
                    if coords:
                        _apply_coordinates(driver, coords, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.5)
            continue

        # ══════════════════════════════════════════════════════
        # GEETEST V3
        # ══════════════════════════════════════════════════════
        if ctype in ("geetest_v3_slide", "geetest_v3_click"):
            gt        = params.get("geetest_gt", "")
            challenge = params.get("geetest_challenge", "")
            api_srv   = params.get("geetest_api_server", "")
            if gt and challenge:
                if progress_cb:
                    progress_cb("🔑 Lấy token GeeTest v3...")
                v3_token = _solve_geetest_v3_token(current_url, gt, challenge, api_srv)
                if v3_token:
                    # GeeTest v3: 2captcha trả về challenge;validate;seccode — inject đủ 3 field
                    if _inject_geetest_v3_token(driver, v3_token):
                        solved_count += 1
                        _human_sleep(1.5, 2.5)
                        continue
                    # Inject thất bại → fallback coordinates
                    logger.warning("[captcha] GeeTest v3 inject thất bại → fallback ảnh")
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    coords = _solve_as_coordinates(img_path)
                    if coords:
                        _apply_coordinates(driver, coords, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.5)
            continue

        # ══════════════════════════════════════════════════════
        # RECAPTCHA (v2 / v3 / Enterprise)
        # ══════════════════════════════════════════════════════
        if ctype in ("recaptcha_v2", "recaptcha_v3", "recaptcha_v2_enterprise", "recaptcha_v3_enterprise"):
            sitekey = params.get("recaptcha_sitekey", "")
            if not sitekey:
                logger.error("[captcha] reCAPTCHA: không tìm thấy sitekey ❌")
                break
            is_v3  = "v3" in ctype
            is_ent = "enterprise" in ctype
            if progress_cb:
                progress_cb(f"🔑 Giải reCAPTCHA {'v3' if is_v3 else 'v2'}{'(enterprise)' if is_ent else ''}...")
            token = _solve_recaptcha_token(
                current_url, sitekey,
                version="v3" if is_v3 else "v2",
                enterprise=is_ent,
                action=params.get("recaptcha_action", "verify"),
            )
            if token:
                _inject_recaptcha_token(driver, token)
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            break

        # ══════════════════════════════════════════════════════
        # HCAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "hcaptcha":
            sitekey = params.get("hcaptcha_sitekey", "")
            if not sitekey:
                logger.error("[captcha] hCaptcha: không tìm thấy sitekey ❌")
                break
            if progress_cb:
                progress_cb("🔑 Giải hCaptcha...")
            token = _solve_hcaptcha_token(current_url, sitekey)
            if token:
                _inject_hcaptcha_token(driver, token)
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            break

        # ══════════════════════════════════════════════════════
        # FUNCAPTCHA / ARKOSE LABS
        # ══════════════════════════════════════════════════════
        if ctype in ("funcaptcha", "funcaptcha_grid", "funcaptcha_compare"):
            pub_key = params.get("funcaptcha_key", "")
            if not pub_key:
                logger.error("[captcha] FunCaptcha: không tìm thấy public key ❌")
                break
            if progress_cb:
                progress_cb("🔑 Giải FunCaptcha/Arkose Labs...")
            token = _solve_funcaptcha_token(current_url, pub_key,
                                             params.get("funcaptcha_surl", ""))
            if token:
                _inject_funcaptcha_token(driver, token)
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            if ctype in ("funcaptcha_grid", "funcaptcha_compare"):
                img_path = _screenshot_rect_to_file(driver, rect)
                if img_path:
                    try:
                        coords = _solve_as_coordinates(img_path)
                        if coords:
                            _apply_coordinates(driver, coords, rect)
                            _press_ok(driver, rect)
                            solved_count += 1
                    finally:
                        try:
                            os.unlink(img_path)
                        except Exception:
                            pass
            break

        # ══════════════════════════════════════════════════════
        # CLOUDFLARE TURNSTILE
        # ══════════════════════════════════════════════════════
        if ctype == "turnstile":
            sitekey = params.get("turnstile_sitekey", "")
            if not sitekey:
                logger.error("[captcha] Turnstile: không tìm thấy sitekey ❌")
                break
            if progress_cb:
                progress_cb("🔑 Giải Cloudflare Turnstile...")
            token = _solve_turnstile_token(current_url, sitekey)
            if token:
                _inject_turnstile_token(driver, token)
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            break

        # ══════════════════════════════════════════════════════
        # CAPY PUZZLE
        # ══════════════════════════════════════════════════════
        if ctype == "capy":
            sitekey = params.get("capy_sitekey", "") or params.get("recaptcha_sitekey", "")
            if sitekey and progress_cb:
                progress_cb("🔑 Giải Capy Puzzle...")
            result = _solve_capy_token(current_url, sitekey) if sitekey else None
            if result:
                tok = _parse_token_dict(result)
                _inject_generic_token(driver, str(tok), ["capy_captchakey"])
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    coords = _solve_as_coordinates(img_path)
                    if coords:
                        _apply_coordinates(driver, coords, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # AMAZON WAF
        # ══════════════════════════════════════════════════════
        if ctype == "amazon_waf":
            sitekey = params.get("amazon_waf_sitekey", "")
            if sitekey:
                if progress_cb:
                    progress_cb("🔑 Giải Amazon WAF CAPTCHA...")
                token = _solve_amazon_waf_token(current_url, sitekey)
                if token:
                    _inject_generic_token(driver, token, ["aws-waf-token"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # DATADOME
        # ══════════════════════════════════════════════════════
        if ctype == "datadome":
            captcha_url = driver.execute_script(
                "return document.querySelector('iframe[src*=\"datadome\"]')?.src||'';"
            ) or ""
            if captcha_url:
                if progress_cb:
                    progress_cb("🔑 Giải DataDome CAPTCHA...")
                token = _solve_datadome_token(current_url, captcha_url)
                if token:
                    _inject_generic_token(driver, token, ["dd_token","datadome"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # MTCAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "mtcaptcha":
            sitekey = params.get("mt_sitekey", "")
            if sitekey:
                if progress_cb:
                    progress_cb("🔑 Giải MTCaptcha...")
                token = _solve_mtcaptcha_token(current_url, sitekey)
                if token:
                    _inject_generic_token(driver, token, ["mtcaptcha-verifiedtoken"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # TENCENT
        # ══════════════════════════════════════════════════════
        if ctype == "tencent":
            app_id = params.get("tencent_app_id", "")
            if app_id:
                if progress_cb:
                    progress_cb("🔑 Giải Tencent CAPTCHA...")
                result = _solve_tencent_token(current_url, app_id)
                if result:
                    tok = _parse_token_dict(result)
                    _inject_generic_token(driver, str(tok.get("ticket", result)),
                                          ["ticket","randstr"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # LEMIN
        # ══════════════════════════════════════════════════════
        if ctype == "lemin":
            lemin_id = params.get("lemin_captcha_id", "")
            if lemin_id:
                if progress_cb:
                    progress_cb("🔑 Giải Lemin CAPTCHA...")
                result = _solve_lemin_token(current_url, lemin_id,
                                             params.get("lemin_div_id", ""))
                if result:
                    tok = _parse_token_dict(result)
                    _inject_generic_token(driver, str(tok), ["lemin-captcha-token"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # CYBERSIARA
        # ══════════════════════════════════════════════════════
        if ctype == "cybersiara":
            master_id = params.get("cybersiara_master_id", "")
            if master_id:
                if progress_cb:
                    progress_cb("🔑 Giải CyberSiARA...")
                token = _solve_cybersiara_token(current_url, master_id)
                if token:
                    _inject_generic_token(driver, token, ["captchaToken"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # CUTCAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "cutcaptcha":
            mb_m = re.search(r'CUTCAPTCHA_MISTERY_BOX\s*=\s*["\']([^"\']+)', driver.page_source)
            ak_m = re.search(r'CUTCAPTCHA_API_KEY\s*=\s*["\']([^"\']+)', driver.page_source)
            if mb_m:
                if progress_cb:
                    progress_cb("🔑 Giải Cutcaptcha...")
                token = _solve_cutcaptcha_token(current_url, mb_m.group(1),
                                                 ak_m.group(1) if ak_m else "")
                if token:
                    _inject_generic_token(driver, token, ["fc-token","captcha-token"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # FRIENDLY CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "friendly_captcha":
            sitekey = params.get("friendly_sitekey", "")
            if sitekey:
                if progress_cb:
                    progress_cb("🔑 Giải Friendly Captcha...")
                token = _solve_friendly_captcha_token(current_url, sitekey)
                if token:
                    _inject_generic_token(driver, token, ["frc-captcha-solution"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # CAPTCHAFOX
        # ══════════════════════════════════════════════════════
        if ctype == "captchafox":
            sitekey = params.get("captchafox_sitekey", "")
            if sitekey:
                if progress_cb:
                    progress_cb("🔑 Giải CaptchaFox...")
                token = _solve_captchafox_token(current_url, sitekey)
                if token:
                    _inject_generic_token(driver, token, ["captchafox-response"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # PROSOPO PROCAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "prosopo":
            sitekey = params.get("prosopo_sitekey", "")
            if sitekey:
                if progress_cb:
                    progress_cb("🔑 Giải Prosopo Procaptcha...")
                token = _solve_prosopo_token(current_url, sitekey)
                if token:
                    _inject_generic_token(driver, token, ["procaptcha-response"])
                    solved_count += 1
                    _human_sleep(1.0, 1.5)
                    continue
            break

        # ══════════════════════════════════════════════════════
        # VK CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "vkcaptcha":
            if progress_cb:
                progress_cb("🔑 Giải VK Captcha...")
            token = _solve_vkcaptcha_token(current_url)
            if token:
                _inject_generic_token(driver, token, ["captcha_sid","captcha_key"])
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    answer = _solve_as_normal(img_path)
                    if answer:
                        _fill_text_input(driver, answer, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # TEMU CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "temu":
            if progress_cb:
                progress_cb("🔑 Giải Temu Captcha...")
            token = _solve_temu_token(current_url)
            if token:
                _inject_generic_token(driver, token, ["captcha_token"])
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            break

        # ══════════════════════════════════════════════════════
        # ALTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "altcha":
            if progress_cb:
                progress_cb("🔑 Giải Altcha...")
            token = _solve_altcha_token(current_url)
            if token:
                _inject_generic_token(driver, token, ["altcha"])
                solved_count += 1
                _human_sleep(1.0, 1.5)
                continue
            break

        # ══════════════════════════════════════════════════════
        # AUDIO CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "audio":
            if progress_cb:
                progress_cb("🔊 Giải Audio CAPTCHA...")
            answer = _solve_as_audio(driver)
            if answer:
                _fill_text_input(driver, answer, rect)
                _press_ok(driver, rect)
                solved_count += 1
                _human_sleep(1.5, 2.0)
                continue
            break

        # ══════════════════════════════════════════════════════
        # ROTATE
        # ══════════════════════════════════════════════════════
        if ctype == "rotate":
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                if progress_cb:
                    progress_cb("🔄 Giải Rotate CAPTCHA...")
                try:
                    angle = _solve_as_rotate(img_path)
                    if angle is not None:
                        _apply_rotate(driver, angle, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                    else:
                        coords = _solve_as_coordinates(img_path)
                        if coords:
                            _apply_coordinates(driver, coords, rect)
                            _press_ok(driver, rect)
                            solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # BOUNDING BOX
        # ══════════════════════════════════════════════════════
        if ctype == "bounding_box":
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                if progress_cb:
                    progress_cb("📦 Giải Bounding Box CAPTCHA...")
                try:
                    hint = driver.execute_script("return document.body.innerText;")[:200]
                    boxes = _solve_as_bounding_box(img_path, hint)
                    if boxes:
                        _inject_generic_token(driver, str(boxes), ["bbox","bounding_box"])
                    _press_ok(driver, rect)
                    solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # DRAW AROUND
        # ══════════════════════════════════════════════════════
        if ctype == "draw_around":
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                if progress_cb:
                    progress_cb("✏️ Giải Draw Around CAPTCHA...")
                try:
                    points = _solve_as_draw_around(img_path)
                    if points:
                        _inject_generic_token(driver, str(points), ["draw_around"])
                    _press_ok(driver, rect)
                    solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # GRID CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype == "grid":
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    tiles = _solve_as_grid(img_path)
                    if tiles:
                        count = driver.execute_script("""
                            for(var s of['[class*="item"]','[class*="tile"]','[class*="cell"]','img']){
                                var n=document.querySelectorAll(s).length;if(n>=4)return n;}return 9;
                        """) or 9
                        _apply_grid_tiles(driver, tiles, rect, count)
                    else:
                        coords = _solve_as_coordinates(img_path)
                        if coords:
                            _apply_coordinates(driver, coords, rect)
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _press_ok(driver, rect)
            solved_count += 1
            _human_sleep(1.5, 2.5)
            continue

        # ══════════════════════════════════════════════════════
        # SLIDE
        # ══════════════════════════════════════════════════════
        if ctype == "slide":
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                try:
                    coords = _solve_as_coordinates(img_path)
                    if coords:
                        _apply_coordinates(driver, coords, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                    else:
                        logger.warning("[captcha] Slide: không có tọa độ ❌")
                        break
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.5)
            continue

        # ══════════════════════════════════════════════════════
        # TEXT / NORMAL IMAGE CAPTCHA
        # ══════════════════════════════════════════════════════
        if ctype in ("text_captcha", "normal_captcha"):
            img_path = _screenshot_rect_to_file(driver, rect)
            if img_path:
                if progress_cb:
                    progress_cb("📝 Giải Text/Normal CAPTCHA...")
                try:
                    answer = _solve_as_normal(img_path)
                    if answer:
                        _fill_text_input(driver, answer, rect)
                        _press_ok(driver, rect)
                        solved_count += 1
                finally:
                    try:
                        os.unlink(img_path)
                    except Exception:
                        pass
            _human_sleep(1.5, 2.0)
            continue

        # ══════════════════════════════════════════════════════
        # CLICK ORDER / UNKNOWN → FALLBACK COORDINATES
        # ══════════════════════════════════════════════════════
        img_path = _screenshot_rect_to_file(driver, rect)
        if not img_path:
            logger.error("[captcha] Không chụp được ảnh ❌")
            break
        try:
            coords = _solve_as_coordinates(img_path)
            if coords:
                _apply_coordinates(driver, coords, rect)
                _press_ok(driver, rect)
                solved_count += 1
            else:
                logger.warning(f"[captcha] {ctype}: không lấy được tọa độ ❌")
                break
        finally:
            try:
                os.unlink(img_path)
            except Exception:
                pass
        _human_sleep(1.5, 2.5)

    else:
        logger.warning(f"[captcha] Hết {MAX_ROUNDS} vòng — thoát")

    return solved_count > 0

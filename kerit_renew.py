#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kerit 免费服务器自动续期脚本
优化版：使用 sing-box 代理（由外部 setup_proxy.sh 管理），
       Turnstile 使用 seleniumbase uc_gui_click_captcha() 内置解法
"""
import os
import time
import json
import imaplib
import email
import re
import subprocess
import urllib.request
import urllib.parse
import requests
from seleniumbase import SB

# ============================================================
# 工具函数
# ============================================================

def mask_email(email_str: str) -> str:
    """掩码邮箱"""
    parts = email_str.split("@")
    local = parts[0]
    domain = parts[1]
    if len(local) > 2:
        return local[0] + "*" * (len(local) - 2) + local[-1] + "@" + domain
    else:
        return local[0] + "*" * max(0, len(local) - 1) + ("" if len(local) == 1 else local[-1]) + "@" + domain


def mask_ip(ip: str) -> str:
    """脱敏 IP 地址"""
    return ip.rsplit(".", 1)[0] + ".***"


# ============================================================
# 配置（从环境变量读取）
# ============================================================

_account = os.environ["KERIT_ACCOUNT"].split(",")
KERIT_EMAIL    = _account[0].strip()
GMAIL_PASSWORD = _account[1].strip()

MASKED_EMAIL = mask_email(KERIT_EMAIL)

LOGIN_URL      = "https://billing.kerit.cloud/"
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"

# ---- 代理配置（sing-box 模式，由 setup_proxy.sh 设置环境变量） ----
IS_PROXY     = os.environ.get("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "socks5://127.0.0.1:1080").strip()

# ---- TG 通知 ----
_tg_raw = os.environ.get("TG_BOT", "")
if _tg_raw and "," in _tg_raw:
    _tg = _tg_raw.split(",")
    TG_CHAT_ID = _tg[0].strip()
    TG_TOKEN   = _tg[1].strip()
else:
    TG_CHAT_ID = ""
    TG_TOKEN   = ""


# ============================================================
# 网络 / IP 检测
# ============================================================

def get_public_ip(proxy_url: str = "") -> str:
    """获取当前出口 IP"""
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        r = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        r.raise_for_status()
        return r.text.strip()
    except Exception:
        return "未知"


def check_ip_info(proxy_url: str = "") -> str:
    """获取 IP 地理位置信息"""
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        r = requests.get(
            "http://ip-api.com/json/?fields=status,query,countryCode",
            proxies=proxies, timeout=30
        ).json()
        if r.get("status") == "success":
            ip_str = f"{mask_ip(r['query'])} ({r['countryCode']})"
            mode = "✅ 代理" if proxy_url else "⚠️ 直连"
            return f"{ip_str} [{mode}]"
    except Exception:
        pass
    mode = "✅ 代理" if proxy_url else "⚠️ 直连"
    return f"未知 IP [{mode}]"


# ============================================================
# TG 推送
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def send_tg(result, server_id=None, remaining=None, ip_info=None, email=None):
    lines = [
        f"🎮 Kerit 服务器续期通知",
        f"🕐 运行时间: {now_str()}",
    ]
    if email:
        tg_user_id = TG_CHAT_ID if TG_CHAT_ID else "0000"
        lines.append(f"📮 邮箱: {email}")
    lines.append(f"📊 续期结果: {result}")
    if server_id is not None:
        lines.append(f"🖥 服务器ID: {server_id}")
    if remaining is not None:
        lines.append(f"⏱️ 剩余天数: {remaining}天")
    if ip_info:
        lines.append(f"🌐 IP信息: {ip_info}")

    msg = "\n".join(lines)
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️ TG未配置，跳过推送")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"📨 TG推送成功")
    except Exception as e:
        print(f"⚠️ TG推送失败：{e}")


# ============================================================
# IMAP 读取 Gmail OTP
# ============================================================

def fetch_otp_from_gmail(wait_seconds=60) -> str:
    print(f"📬 连接Gmail，等待{wait_seconds}s...")
    deadline = time.time() + wait_seconds

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(KERIT_EMAIL, GMAIL_PASSWORD)
    except imaplib.IMAP4.error as e:
        print(f"❌ Gmail 认证失败: {e}")
        print("💡 请检查: 1.KERIT_ACCOUNT 环境变量  2.Gmail IMAP 已开启  3.使用应用专用密码")
        raise TimeoutError(f"Gmail 认证失败: {e}")

    # 查找垃圾箱
    spam_folder = None
    _, folder_list = mail.list()
    for f in folder_list:
        decoded = f.decode("utf-8", errors="ignore")
        if any(k in decoded for k in ["Spam", "Junk", "垃圾", "spam", "junk"]):
            match = re.search(r'"([^"]+)"\s*$', decoded)
            if not match:
                match = re.search(r'(\S+)\s*$', decoded)
            if match:
                spam_folder = match.group(1).strip('"')
                print(f"🗑️ 检查Gmail垃圾邮箱")
                break

    folders_to_check = ["INBOX"]
    if spam_folder:
        folders_to_check.append(spam_folder)
    else:
        print("⚠️ 未找到垃圾邮箱")

    # 记录已见 UID
    seen_uids = {}
    for folder in folders_to_check:
        try:
            mail.select(folder)
            _, data = mail.uid("search", None, "ALL")
            seen_uids[folder] = set(data[0].split())
        except Exception as e:
            print(f"⚠️ 文件夹异常 {folder}: {e}")
            seen_uids[folder] = set()

    while time.time() < deadline:
        time.sleep(5)
        for folder in folders_to_check:
            try:
                mail.select(folder)
                _, data = mail.uid("search", None, 'FROM "kerit"')
                all_uids = set(data[0].split())
                new_uids = all_uids - seen_uids[folder]

                for uid in new_uids:
                    seen_uids[folder].add(uid)
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                        if not body:
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                    body = re.sub(r'<[^>]+>', ' ', html)
                                    break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    otp = re.search(r'\b(\d{4})\b', body)
                    if otp:
                        code = otp.group(1)
                        print(f"✅ Gmail OTP: {code}")
                        mail.logout()
                        return code
            except Exception as e:
                print(f"⚠️ 检查 {folder} 出错: {e}")
                continue

    mail.logout()
    raise TimeoutError("❌ Gmail超时，未收到 OTP 邮件")


# ============================================================
# Turnstile 工具函数（seleniumbase UC 模式内置解法）
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return;
    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }
    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });
})();
"""

CF_INDICATORS = [
    "verify you are human",
    "确认您是真人",
    "just a moment...",
    "checking your browser",
    "troubleshoot",
    "cf-chl",
    "challenges.cloudflare.com",
]


def check_token(sb) -> bool:
    """检查 Turnstile token 是否已填充"""
    try:
        return sb.execute_script("""
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return input && input.value && input.value.length > 20;
            })()
        """)
    except Exception:
        return False


def get_token_value(sb) -> str:
    """获取 Turnstile token"""
    try:
        token = sb.execute_script("""
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return (input && input.value) ? input.value : '';
            })()
        """)
        if token and len(token) > 20:
            return token
    except Exception:
        pass
    return ''


def turnstile_exists(sb) -> bool:
    """检测页面上是否有 Turnstile"""
    try:
        return sb.execute_script(
            "(function(){ return document.querySelector('input[name=\"cf-turnstile-response\"]') !== null; })()"
        )
    except Exception:
        return False


def wait_for_turnstile_pass(sb, timeout=30) -> bool:
    """等待 Turnstile/CF 挑战通过"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            if check_token(sb):
                print("✅ Turnstile 验证已通过（token 已填充）")
                return True
            page_lower = sb.get_page_source().lower()
            if not any(k in page_lower for k in CF_INDICATORS):
                print("✅ Turnstile 验证已通过（页面已离开挑战页）")
                return True
        except Exception:
            pass
        sb.sleep(1)
    print("❌ Turnstile 验证超时未通过")
    return False


def solve_turnstile(sb) -> bool:
    """使用 seleniumbase uc_gui_click_captcha() 点击 Turnstile（eooce 风格）"""
    # 撑开 iframe 便于点击
    try:
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.5)
    except Exception:
        pass

    if check_token(sb):
        print("✅ Token 已存在")
        return True

    for attempt in range(1, 4):
        print(f"🖱️ 尝试点击 Turnstile ({attempt}/3)...")
        try:
            sb.uc_gui_click_captcha()
            time.sleep(12)  # 等待 JS 验证
        except Exception as e:
            print(f"⚠️ uc_gui_click_captcha 出错: {e}")
            time.sleep(2)

        if wait_for_turnstile_pass(sb, timeout=20):
            print("✅ Cloudflare Token 通过")
            return True
        else:
            print(f"⏳ 第 {attempt} 次未通过，重试...")

    print("❌ Cloudflare Token 超时")
    sb.save_screenshot("turnstile_fail.png")
    return False


def extract_remaining_days(sb) -> int:
    """从 expiry-display 元素读取剩余天数"""
    try:
        return sb.execute_script("""
            (function(){
                var el = document.getElementById('expiry-display');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        """) or 0
    except Exception:
        return 0


# ============================================================
# 续期流程
# ============================================================

def do_renew(sb, ip_info=None, email=None):
    print("🔄 跳转续期页...")
    sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=4)
    time.sleep(4)
    sb.save_screenshot("free_panel.png")

    server_id = sb.execute_script(
        "(function(){ return typeof serverData !== 'undefined' ? serverData.id : null; })()"
    )
    if not server_id:
        print("❌ serverData.id 缺失")
        sb.save_screenshot("no_server_id.png")
        send_tg("❌ serverData.id 缺失，续期失败", ip_info=ip_info, email=email)
        return
    print(f"🆔 服务器ID: {server_id}")

    initial_count = sb.execute_script("""
        (function(){
            var el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        })()
    """)
    initial_remaining = extract_remaining_days(sb)
    need = 7 - initial_count
    print(f"📊 当前进度: {initial_count}/7，剩余天数: {initial_remaining}天，本次需续期: {need}次")

    if initial_remaining >= 7:
        print("✅ 剩余天数已满7天，无需续期")
        sb.save_screenshot("renew_skip.png")
        send_tg("✅ 无需续期（剩余天数已满）", server_id, initial_remaining, ip_info=ip_info, email=email)
        return

    if need <= 0:
        print("🎉 已达上限 7/7，无需续期")
        sb.save_screenshot("renew_full.png")
        remaining = extract_remaining_days(sb)
        send_tg("✅ 无需续期（已达上限 7/7）", server_id, remaining, ip_info=ip_info, email=email)
        return

    for attempt in range(need):
        count = sb.execute_script("""
            (function(){
                var el = document.getElementById('renewal-count');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        """)
        print(f"📊 续期进度: {count}/7")

        if count >= 7:
            print("🎉 已达上限 7/7，提前结束")
            sb.save_screenshot("renew_full.png")
            remaining = extract_remaining_days(sb)
            send_tg("✅ 续期完成", server_id, remaining, ip_info=ip_info, email=email)
            return

        print(f"🔁 第{attempt + 1}/{need}次续期...")

        # 点击 Renew Server 按钮
        renew_clicked = False
        for _ in range(10):
            try:
                btns = sb.find_elements("a, button")
                btn = next((b for b in btns if "Renew Server" in (b.text or "")), None)
                if btn:
                    btn.click()
                    renew_clicked = True
                    print("✅ 已点击「Renew Server」")
                    break
            except Exception:
                pass
            time.sleep(1)

        if not renew_clicked:
            print("❌ 续期按钮缺失")
            sb.save_screenshot("no_renew_btn.png")
            send_tg(f"❌ 续期按钮缺失，第{attempt + 1}次失败", server_id, ip_info=ip_info, email=email)
            return

        time.sleep(2)

        print("⏳ 等待 Turnstile...")
        for _ in range(20):
            if turnstile_exists(sb):
                print("🛡️ 检测到 Turnstile")
                break
            time.sleep(1)
        else:
            print("❌ Turnstile 未出现")
            sb.save_screenshot(f"no_turnstile_{attempt}.png")
            send_tg(f"❌ Turnstile 未出现，第{attempt + 1}次失败", server_id, ip_info=ip_info, email=email)
            return

        if not solve_turnstile(sb):
            sb.save_screenshot(f"turnstile_fail_{attempt}.png")
            send_tg(f"❌ Turnstile 验证失败，第{attempt + 1}次", server_id, ip_info=ip_info, email=email)
            return

        token = get_token_value(sb)
        if not token:
            print("❌ Token 获取失败")
            send_tg(f"❌ Token 获取失败，第{attempt + 1}次", server_id, ip_info=ip_info, email=email)
            return

        print("🎯 提交续期...")
        result = sb.execute_script(f"""
            (async function() {{
                const res = await fetch('/api/renew', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'include',
                    body: JSON.stringify({{ id: '{server_id}', captcha: '{token}' }})
                }});
                const data = await res.json();
                return JSON.stringify(data);
            }})()
        """)
        try:
            import json as _json
            res_obj = _json.loads(result)
            if res_obj.get('success') or res_obj == {}:
                print("✅ 续期成功")
            else:
                print(f"❌ 续期失败: {result}")
        except Exception:
            print(f"✅ 续期成功")

        # 关闭弹窗
        try:
            sb.execute_script("document.querySelector('[data-bs-dismiss=\"modal\"]')?.click();")
        except Exception:
            pass

        time.sleep(3)
        sb.execute_script("window.location.reload();")
        time.sleep(3)

    sb.save_screenshot("renew_done.png")
    final_count = sb.execute_script("""
        (function(){
            var el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        })()
    """)
    final_remaining = extract_remaining_days(sb)
    print(f"📊 最终进度: {final_count}/7")
    if final_count >= 7:
        print("🎉 已达上限 7/7")
        send_tg("✅ 续期完成", server_id, final_remaining, ip_info=ip_info, email=email)
    else:
        print(f"⚠️ 续期未达上限，当前{final_count}/7")
        send_tg(f"⚠️ 续期未达上限（{final_count}/7）", server_id, final_remaining, ip_info=ip_info, email=email)


# ============================================================
# 主流程
# ============================================================

def run_script():
    # ── 代理信息 ──
    proxy_url = PROXY_SERVER if IS_PROXY else ""
    if IS_PROXY:
        print(f"🔗 使用代理: {proxy_url}")
    else:
        print("🍭 直连模式（未使用代理）")

    # ── 出口 IP ──
    try:
        ip = get_public_ip(proxy_url)
        print(f"📍 当前出口IP: {ip}")
    except Exception as e:
        print(f"⚠️ 获取出口 IP 失败: {e}")

    ip_info = check_ip_info(proxy_url)
    print(f"🌐 IP 信息: {ip_info}")

    # ── 启动浏览器 ──
    sb_kwargs = {"uc": True, "test": True}
    if IS_PROXY:
        sb_kwargs["proxy"] = proxy_url

    try:
        with SB(**sb_kwargs) as sb:
            print("🚀 浏览器就绪！")

            # ── IP 验证 ──
            print("🌐 验证出口IP...")
            try:
                sb.open("https://api.ipify.org/?format=json")
                ip_text = sb.get_text('body')
                ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', ip_text)
                print(f"✅ 出口IP确认：{ip_text}")
            except Exception:
                print("⚠️ IP验证超时，跳过")

            # ── 登录 ──
            print("🔑 打开登录页面...")
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
            time.sleep(3)

            # 说明：登录页可能遇到 Cloudflare JS 挑战（challenges.cloudflare.com）
            # 但这不是 Turnstile widget（没有 cf-turnstile-response input），
            # 所以这里不检查 Turnstile，而是直接等待邮箱框出现，让 UC 模式自动处理挑战。
            print("📭 等待邮箱框（UC 模式自动过 CF 挑战）...")
            email_loaded = False
            for _ in range(30):  # 最多等 90 秒
                try:
                    if sb.is_element_visible('#email-input'):
                        email_loaded = True
                        break
                except Exception:
                    pass
                # 若页面仍停留在 CF 挑战，打印诊断信息
                try:
                    page_lower = sb.get_page_source().lower()
                    if "performing security" in page_lower or "just a moment" in page_lower or "verify you are human" in page_lower:
                        # 尝试重新连接让 UC 模式再次处理
                        if _ % 10 == 0:
                            print(f"⏳ 检测到 CF 挑战页，等待 UC 模式处理... ({( _ + 1) * 3}s)")
                            try:
                                sb.uc_gui_click_captcha()
                            except Exception:
                                pass
                except Exception:
                    pass
                time.sleep(3)

            if not email_loaded:
                print("❌ 邮箱框加载失败（可能 CF 挑战未通过）")
                sb.save_screenshot("kerit_no_email_input.png")
                try:
                    with open("kerit_page_source.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                    print("📄 已保存页面源码: kerit_page_source.html")
                except Exception:
                    pass
                send_tg("❌ 邮箱框加载失败（CF 挑战或页面结构变化）", ip_info=ip_info, email=MASKED_EMAIL)
                return

            sb.type('#email-input', KERIT_EMAIL)
            print(f"✅ 邮箱：{MASKED_EMAIL}")

            print("🖱️ 点击继续...")
            clicked = False
            for sel in [
                '//button[contains(., "Continue with Email")]',
                '//span[contains(., "Continue with Email")]',
                'button[type="submit"]',
            ]:
                try:
                    if sb.is_element_visible(sel):
                        sb.click(sel)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                print("❌ 继续按钮缺失")
                sb.save_screenshot("kerit_no_continue_btn.png")
                send_tg("❌ 继续按钮缺失", ip_info=ip_info, email=MASKED_EMAIL)
                return

            # 等待页面切换（从邮箱输入页到 OTP 页），最多等 60 秒
            print("📨 等待 OTP 框...")
            otp_loaded = False
            otp_selectors = ['.otp-input', 'input[autocomplete="one-time-code"]', 'input[type="tel"]', 'input[data-testid*="otp"]', 'input[class*="otp"]', 'input[class*="code"]']
            for _ in range(60):
                try:
                    for sel in otp_selectors:
                        if sb.is_element_visible(sel):
                            otp_loaded = True
                            break
                    if otp_loaded:
                        break
                except Exception:
                    pass
                # 检查当前页面是否还在邮箱输入页
                try:
                    if sb.is_element_visible('#email-input'):
                        if _ % 10 == 0:
                            print(f"⏳ 仍在邮箱页，等待 OTP 页面加载... ({_ + 1}s)")
                except Exception:
                    pass
                time.sleep(1)

            if not otp_loaded:
                print("❌ OTP 框加载失败")
                sb.save_screenshot("kerit_no_otp.png")
                try:
                    with open("kerit_otp_page.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                    print("📄 已保存页面源码: kerit_otp_page.html")
                except Exception:
                    pass
                send_tg("❌ OTP 框加载失败（页面结构变化或按钮未生效）", ip_info=ip_info, email=MASKED_EMAIL)
                return

            # 先等 OTP 稳定了再取邮件，给 Gmail 多几秒
            time.sleep(2)
            try:
                code = fetch_otp_from_gmail(wait_seconds=60)
            except TimeoutError as e:
                print(e)
                sb.save_screenshot("kerit_otp_timeout.png")
                send_tg("❌ Gmail OTP 获取超时", ip_info=ip_info, email=MASKED_EMAIL)
                return

            # 查找 OTP 输入框（多个选择器兜底）
            otp_selector = '.otp-input'
            otp_inputs = sb.find_elements(otp_selector)
            if len(otp_inputs) < 4:
                for sel in ['input[autocomplete="one-time-code"]', 'input[type="tel"]', 'input[data-testid*="otp"]', 'input[class*="otp"]', 'input[class*="code"]']:
                    otp_inputs = sb.find_elements(sel)
                    if len(otp_inputs) >= 4:
                        otp_selector = sel
                        break
            if len(otp_inputs) < 4:
                # 兜底：用 JS 找所有 visible input
                try:
                    js_otp = sb.execute_script("""
                        (function(){
                            var inputs = document.querySelectorAll('input[type="text"], input:not([type="hidden"]):not([type="email"]):not([type="password"])');
                            var visible = [];
                            for (var i = 0; i < inputs.length; i++) {
                                if (inputs[i].offsetParent !== null && inputs[i].offsetWidth > 0) {
                                    visible.push(inputs[i]);
                                }
                            }
                            return visible.length;
                        })()
                    """)
                    print(f"📊 JS 查询可见 input 数量: {js_otp}")
                    if js_otp >= 4:
                        otp_selector = 'JS_FALLBACK'
                except Exception:
                    pass

            if len(otp_inputs) < 4 and otp_selector != 'JS_FALLBACK':
                print(f"❌ OTP 框不足: {len(otp_inputs)}")
                send_tg(f"❌ OTP 框数量不足（{len(otp_inputs)}）", ip_info=ip_info, email=MASKED_EMAIL)
                return

            print(f"⌨️ 填入 OTP: {code} (选择器: {otp_selector})")
            for i, char in enumerate(code):
                if otp_selector == 'JS_FALLBACK':
                    # 用 JS 找到第 i 个可见 input
                    js = f"""
                        (function() {{
                            var inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="email"]):not([type="password"])');
                            var visible = [];
                            for (var j = 0; j < inputs.length; j++) {{
                                if (inputs[j].offsetParent !== null && inputs[j].offsetWidth > 0) {{
                                    visible.push(inputs[j]);
                                }}
                            }}
                            var inp = visible[{i}];
                            if (!inp) return;
                            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(inp, '{char}');
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                        }})();
                    """
                else:
                    js = f"""
                        (function() {{
                            var inputs = document.querySelectorAll('{otp_selector}');
                            var inp = inputs[{i}];
                            if (!inp) return;
                            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(inp, '{char}');
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                        }})();
                    """
                sb.execute_script(js)
                time.sleep(0.1)

            print("✅ OTP 已填入")
            time.sleep(0.5)

            print("🚀 点击验证...")
            verify_clicked = False
            for sel in [
                '//button[contains(., "Verify Code")]',
                '//span[contains(., "Verify Code")]',
                'button[type="submit"]',
            ]:
                try:
                    if sb.is_element_visible(sel):
                        sb.click(sel)
                        verify_clicked = True
                        break
                except Exception:
                    continue

            if not verify_clicked:
                print("❌ 验证按钮缺失")
                sb.save_screenshot("kerit_no_verify_btn.png")
                send_tg("❌ 验证按钮缺失", ip_info=ip_info, email=MASKED_EMAIL)
                return

            print("⏳ 等待登录跳转...")
            for _ in range(80):
                try:
                    url = sb.get_current_url()
                    if "/session" in url:
                        print("✅ 登录成功！")
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            else:
                print("❌ 登录等待超时")
                sb.save_screenshot("kerit_login_timeout.png")
                send_tg("❌ 登录等待超时", ip_info=ip_info, email=MASKED_EMAIL)
                return

            do_renew(sb, ip_info, MASKED_EMAIL)
    except Exception as e:
        print(f"❌ 脚本异常: {e}")
        import traceback
        traceback.print_exc()
        send_tg(f"❌ 脚本异常: {e}", ip_info=ip_info, email=MASKED_EMAIL)


if __name__ == "__main__":
    run_script()

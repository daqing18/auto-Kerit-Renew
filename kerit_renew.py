#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kerit 免费服务器自动续期脚本
优化版：使用 sing-box 代理（端口 8080 mixed inbound），
       Turnstile 使用 katabump 风格的过盾逻辑（展开 iframe + uc_gui_click_captcha 多次轮询）
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
import socket
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
# 配置（从环境变量读取，匹配 GitHub Secrets）
# ============================================================

KERIT_EMAIL    = os.environ.get("EMAIL", "").strip()
GMAIL_PASSWORD = os.environ.get("PASSWORD", "").strip()
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID", "").strip()
TG_TOKEN       = os.environ.get("TG_TOKEN", "").strip()

MASKED_EMAIL = mask_email(KERIT_EMAIL) if KERIT_EMAIL else ""

LOGIN_URL      = "https://billing.kerit.cloud/"
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"

# ---- Cookie 登录（可选，跳过 CF 挑战和邮箱/OTP 流程） ----
KERIT_COOKIE_CF_CLEARANCE = os.environ.get("KERIT_COOKIE_CF_CLEARANCE", "").strip()
KERIT_COOKIE_SESSION_ID   = os.environ.get("KERIT_COOKIE_SESSION_ID", "").strip()
USE_COOKIE_LOGIN = bool(KERIT_COOKIE_CF_CLEARANCE and KERIT_COOKIE_SESSION_ID)

# ---- 代理配置 ----
# sing-box 生成的 config.json 使用 127.0.0.1:8080 mixed inbound
# 优先检测 8080，再回退 1081 / 1080
PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip()
NODE_LINK    = os.environ.get("NODE_LINK", "").strip()

def _port_open(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except Exception:
        return False

if not PROXY_SERVER and NODE_LINK:
    if _port_open(8080):
        PROXY_SERVER = "http://127.0.0.1:8080"
    elif _port_open(1081):
        PROXY_SERVER = "http://127.0.0.1:1081"
    else:
        PROXY_SERVER = "socks5://127.0.0.1:1080"

IS_PROXY = bool(PROXY_SERVER)

# ---- TG 通知 ----
if not TG_CHAT_ID and not TG_TOKEN:
    _tg_raw = os.environ.get("TG_BOT", "")
    if _tg_raw and "," in _tg_raw:
        _tg = _tg_raw.split(",")
        TG_CHAT_ID = _tg[0].strip()
        TG_TOKEN   = _tg[1].strip()

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
        print("💡 请检查: 1.EMAIL/PASSWORD 环境变量  2.Gmail IMAP 已开启  3.使用应用专用密码")
        raise TimeoutError(f"Gmail 认证失败: {e}")

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
# Turnstile 工具函数（katabump 风格：展开 iframe + 多次轮询）
# ============================================================

_TURNSTILE_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_TURNSTILE_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_TURNSTILE_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
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
    try:
        return sb.execute_script(_TURNSTILE_SOLVED_JS)
    except Exception:
        return False

def get_token_value(sb) -> str:
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
    try:
        return sb.execute_script(_TURNSTILE_EXISTS_JS)
    except Exception:
        return False

def wait_for_turnstile_pass(sb, timeout=30) -> bool:
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

def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    if check_token(sb):
        print("✅ 已静默通过")
        return True

    for _ in range(3):
        try:
            sb.execute_script(_TURNSTILE_EXPAND_JS)
        except Exception:
            pass
        time.sleep(0.5)

    for attempt in range(6):
        if check_token(sb):
            print(f"✅ Turnstile 通过（第 {attempt} 次尝试前已通过）")
            return True

        print(f"🖱️ 第 {attempt + 1} 次调用 uc_gui_click_captcha...")
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"⚠️ uc_gui_click_captcha 调用异常: {e}")

        for _ in range(16):
            time.sleep(0.5)
            if check_token(sb):
                print(f"✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True

        print(f"⚠️ 第 {attempt + 1} 次未通过，重试...")

    print("  ❌ Turnstile 6 次均失败")
    return False

def solve_turnstile(sb) -> bool:
    return handle_turnstile(sb)

def extract_remaining_days(sb) -> int:
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
    sb.open(FREE_PANEL_URL)
    time.sleep(5)

    title = sb.get_title() or ""
    page_lower = sb.get_page_source().lower()
    is_cf = "just a moment" in title.lower() or "performing security verification" in page_lower

    if is_cf:
        print("⏳ 检测到 CF 挑战页，等待清除...")
        for _ in range(12):
            time.sleep(5)
            try:
                t = sb.get_title() or ""
                pl = sb.get_page_source().lower()
                if "just a moment" not in t.lower() and "performing security" not in pl:
                    print("✅ CF 已清除")
                    break
            except Exception:
                pass

    sb.save_screenshot("free_panel.png")

    server_id = sb.execute_script(
        "(function(){ return typeof serverData !== 'undefined' ? serverData.id : null; })()"
    )
    if not server_id:
        print("⏳ serverData.id 缺失，等待异步加载...")
        for i in range(10):
            time.sleep(2)
            server_id = sb.execute_script(
                "(function(){ return typeof serverData !== 'undefined' ? serverData.id : null; })()"
            )
            if server_id:
                print(f"✅ 异步加载完成，服务器ID: {server_id}")
                break
        if not server_id:
            diag = sb.execute_script("""
                (function(){
                    var result = {};
                    if (typeof serverData !== 'undefined') {
                        result.serverData = JSON.stringify(serverData).substring(0, 500);
                    }
                    result.globals = Object.keys(window).filter(k =>
                        ['data','server','panel','user','config','state'].some(s =>
                            k.toLowerCase().includes(s)
                        )
                    ).slice(0, 20);
                    result.title = document.title;
                    result.text = document.body ? document.body.innerText.substring(0, 800) : '';
                    var s = document.querySelector('#__NEXT_DATA__');
                    if (s) result.next_data = JSON.stringify(s.textContent).substring(0, 500);
                    return JSON.stringify(result);
                })()
            """)
            print(f"🔍 页面诊断: {diag}")
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
    proxy_url = PROXY_SERVER if IS_PROXY else ""
    if IS_PROXY:
        print(f"🔗 使用代理: {proxy_url}")
    else:
        print("🍭 直连模式（未使用代理）")

    try:
        ip = get_public_ip(proxy_url)
        print(f"📍 当前出口IP: {ip}")
    except Exception as e:
        print(f"⚠️ 获取出口 IP 失败: {e}")

    ip_info = check_ip_info(proxy_url)
    print(f"🌐 IP 信息: {ip_info}")

    sb_kwargs = {"uc": True, "test": True}
    if IS_PROXY:
        sb_kwargs["proxy"] = proxy_url

    try:
        with SB(**sb_kwargs) as sb:
            print("🚀 浏览器就绪！")

            print("🌐 验证出口IP...")
            try:
                sb.open("https://api.ipify.org/?format=json")
                ip_text = sb.get_text('body')
                ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', ip_text)
                print(f"✅ 出口IP确认：{ip_text}")
            except Exception:
                print("⚠️ IP验证超时，跳过")

            login_ok = False

            if USE_COOKIE_LOGIN:
                print("🍪 尝试 Cookie 注入登录...")
                try:
                    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
                    time.sleep(2)
                    sb.add_cookie({"name": "cf_clearance", "value": KERIT_COOKIE_CF_CLEARANCE,
                                   "domain": ".kerit.cloud", "path": "/"})
                    sb.add_cookie({"name": "session_id", "value": KERIT_COOKIE_SESSION_ID,
                                   "domain": ".kerit.cloud", "path": "/"})
                    print("🌐 重新加载登录页验证 cookie...")
                    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
                    time.sleep(5)
                    current_url = sb.get_current_url()
                    print(f"📝 当前URL: {current_url}")
                    title = sb.get_title() or ""
                    page_lower = sb.get_page_source().lower()
                    is_cf = "just a moment" in title.lower() or "performing security verification" in page_lower
                    if is_cf:
                        print("⏳ 等待 CF 挑战清除...")
                        for _ in range(6):
                            time.sleep(5)
                            try:
                                t = sb.get_title() or ""
                                pl = sb.get_page_source().lower()
                                if "just a moment" not in t.lower() and "performing security" not in pl:
                                    print("✅ CF 已清除")
                                    break
                            except Exception:
                                pass
                    current_url = sb.get_current_url()
                    if "login" not in current_url and "otp" not in current_url.lower():
                        print("✅ Cookie 登录成功！")
                        login_ok = True
                    else:
                        print(f"❌ Cookie 登录失败，当前URL: {current_url}")
                except Exception as e:
                    print(f"❌ Cookie 注入失败: {e}")

            if not login_ok:
                if USE_COOKIE_LOGIN:
                    print("🔄 Cookie 登录失败，回退到邮箱/OTP 登录...")
                print("🔑 打开登录页面...")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
                time.sleep(3)

                print("📭 等待邮箱框（UC 模式自动过 CF 挑战）...")
                email_loaded = False
                for _ in range(30):
                    try:
                        if sb.is_element_visible('#email-input'):
                            email_loaded = True
                            break
                    except Exception

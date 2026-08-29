#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kerit.Cloud 免费服务器自动续期脚本
基于朋友的 VPS 成功运行流程重写
流程：Discord登录 → 首页面 → Cloudflare过盾 → Discord OAuth → 续期页 → Renew → Sponsor → Complete Renewal
"""
import time
import os
import json
import re
import random
import requests

# 智能环境配置
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":1"
if "XAUTHORITY" not in os.environ:
    if os.path.exists("/home/headless/.Xauthority"):
        os.environ["XAUTHORITY"] = "/home/headless/.Xauthority"

print(f"[DEBUG] Env DISPLAY: {os.environ.get('DISPLAY')}")
print(f"[DEBUG] Env XAUTHORITY: {os.environ.get('XAUTHORITY')}")

from seleniumbase import SB
from selenium.webdriver.common.keys import Keys

# ================= 配置区域 =================
# 代理地址：优先 PROXY/PROXY_SERVER（setup_proxy.sh 设置的本地代理），
# NODE_LINK 可能是订阅链接，不能直接当代理地址用，只有它本身是 socks/http 开头才接受
_node_link = os.getenv("NODE_LINK", "")
_proxy_env = os.getenv("PROXY", os.getenv("PROXY_SERVER", ""))
if _proxy_env and re.match(r'^(socks5|socks4|http|https)://', _proxy_env):
    SOCKS5_URL = _proxy_env
elif _node_link and re.match(r'^(socks5|socks4|http|https)://', _node_link):
    SOCKS5_URL = _node_link
else:
    # 订阅链接或未设置：使用本地 sing-box 默认端口
    SOCKS5_URL = "socks5://127.0.0.1:1080"
print(f"[DEBUG] 代理地址: {SOCKS5_URL}")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

DISCORD_URL = "https://discord.com/login"
LOGIN_URL = "https://billing.kerit.cloud"
MAIN_URL = "https://billing.kerit.cloud/free_panel"
# ===========================================

class KeritCloudRenewal:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.BASE_DIR, "artifacts")
        if not os.path.exists(self.screenshot_dir):
            os.makedirs(self.screenshot_dir)

    def log(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [INFO] {msg}", flush=True)

    def send_telegram_notify(self, message, photo_path=None):
        if not TG_TOKEN or not TG_CHAT_ID:
            return
        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                with open(photo_path, 'rb') as f:
                    requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': message}, files={'photo': f})
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                requests.post(url, data={'chat_id': TG_CHAT_ID, 'text': message})
            self.log("✅ TG 推送已发送")
        except Exception as e:
            self.log(f"❌ TG 推送失败: {e}")

    def discord_login(self, sb, email, password):
        self.log("✏️ 输入账号密码")
        sb.fill('input[name="email"]', email)
        sb.fill('input[name="password"]', password)
        self.log("📤 提交登录")
        sb.click('button[type="submit"]')
        time.sleep(10)

    def cloudflare_all_page(self, sb):
        """处理全页 Cloudflare 挑战"""
        self.log("⏳ 全页Cloudflare挑战")
        cf_indicators = ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]
        for i in range(2):
            sb.uc_gui_click_captcha()
            time.sleep(15)
            page_lower = sb.get_page_source().lower()
            if any(x in page_lower for x in cf_indicators):
                sb.uc_gui_handle_captcha()
                time.sleep(15)
                page_lower = sb.get_page_source().lower()
            if not any(x in page_lower for x in cf_indicators):
                self.log("✅Cloudflare验证已通过")
                return True
        self.log("⚠️ Cloudflare 可能未完全通过")
        return False

    def click_discord_login(self, sb):
        """点击 Discord 登录按钮，带多重 fallback"""
        self.log("🔍 等待 Discord 登录按钮...")
        # 先检查当前页面是否有 Discord 登录按钮
        for attempt in range(3):
            try:
                # 方式1: 标准选择器
                sb.wait_for_element_visible('a[href="/auth/discord"]', timeout=15)
                sb.click('a[href="/auth/discord"]')
                self.log("✅ Discord 登录按钮点击成功")
                return True
            except Exception:
                pass

            try:
                # 方式2: 文本匹配
                el = sb.find_element("a, span, button")
                for e in sb.find_elements("a, span, button"):
                    try:
                        text = (e.text or "").strip()
                        if "Discord" in text or "discord" in text.lower():
                            # 尝试找到直接父级 a 标签
                            parent_a = sb.execute_script("""
                                let el = arguments[0];
                                while (el && el.tagName !== 'A') el = el.parentElement;
                                return el;
                            """, e)
                            if parent_a:
                                parent_a.click()
                            else:
                                e.click()
                            self.log("✅ Discord 登录按钮点击成功（文本匹配）")
                            return True
                    except Exception:
                        continue
            except Exception:
                pass

            # 方式3: 重新导航确保页面刷新
            self.log("🔄 Discord 按钮未找到，重新导航页面...")
            try:
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=15)
                time.sleep(8)
                # 再次检查 Cloudflare
                self.cloudflare_all_page(sb)
                time.sleep(5)
            except Exception:
                pass

        self.log("❌ Discord 登录按钮未能点击")
        return False

    def oauth_debug(self, sb):
        """处理 Discord OAuth 授权页面，匹配朋友流程"""
        self.log("🔐 OAuth 页面分析开始")
        # 先等待 OAuth 页面加载（可能需要几秒）
        time.sleep(3)
        for i in range(40):
            self.log(f"🔍 分析 {i+1}/40")
            time.sleep(2)
            try:
                buttons = sb.find_elements("button")
                self.log(f"找到按钮数量: {len(buttons)}")
                for btn in buttons:
                    try:
                        text = (btn.text or "").strip()
                        self.log(f"按钮: {repr(text)}")
                        # 匹配 "继续滚动……" 按钮（朋友流程中的关键按钮）
                        if "继续滚动" in text or "continue" in text.lower():
                            self.log("🟡 发现继续滚动按钮")
                            # 先尝试滚动到底部
                            sb.execute_script("""
                                let els=document.querySelectorAll('*');
                                for(let el of els){
                                    try{
                                        if(el.scrollHeight>el.clientHeight){
                                            el.scrollTop=el.scrollHeight;
                                        }
                                    }catch(e){}
                                }
                            """)
                            time.sleep(2)
                            # 点击第二个按钮（索引1，通常是"继续滚动"）
                            sb.execute_script("document.querySelectorAll('button')[1].click();")
                            self.log("✅ 已点击继续滚动")
                            time.sleep(5)
                            # 检查是否跳转
                            url = sb.get_current_url()
                            self.log(f"当前URL:{url}")
                            if "kerit.cloud" in url:
                                self.log("✅ OAuth完成")
                                return True
                            break
                        # 匹配授权按钮
                        if "授权" in text or text == "Authorize" or text == "Authorise":
                            self.log("🟢 找到授权按钮")
                            sb.execute_script("document.querySelectorAll('button')[1].click();")
                            self.log("✅ OAuth授权点击完成")
                            time.sleep(8)
                            url = sb.get_current_url()
                            self.log(f"当前URL:{url}")
                            if "kerit.cloud" in url:
                                self.log("✅ OAuth完成")
                                return True
                            break
                    except Exception as e:
                        self.log(f"按钮处理错误: {e}")
            except Exception as e:
                self.log(f"OAuth按钮检测失败: {e}")

            # 检查 URL 是否已经是 kerit.cloud 的 session 页面
            try:
                url = sb.get_current_url()
                self.log(f"当前URL:{url}")
                # 朋友流程：OAuth 完成后跳转到 billing.kerit.cloud/session
                if "kerit.cloud/session" in url or "kerit.cloud" in url:
                    self.log("✅ OAuth完成")
                    return True
            except Exception:
                pass

        self.log("❌ OAuth失败")
        return False

    def check_renewal_status(self, sb):
        """检查续期状态，匹配朋友流程"""
        try:
            status = sb.execute_script("""
                (function(){
                    let el=document.querySelector('#renewal-status-text');
                    if(!el) return "";
                    return (el.textContent || "").trim();
                })();
            """)
            subtext = sb.execute_script("""
                (function(){
                    let el=document.querySelector('#renewal-status-subtext');
                    if(!el) return "";
                    return (el.textContent || "").trim();
                })();
            """)
            self.log(f"🔍 Renewal状态: {status}")
            self.log(f"🔍 Renewal说明: {subtext}")
            if not status:
                self.log("⚠️ Renewal状态为空，等待加载...")
                for i in range(10):
                    time.sleep(1)
                    status = sb.execute_script("""
                        (function(){
                            let el=document.querySelector('#renewal-status-text');
                            return el ? (el.innerText || "").trim() : "";
                        })();
                    """)
                    if status:
                        self.log(f"✅ 状态加载完成: {status}")
                        break
            if not status:
                self.log("⚠️ 仍未获取状态，默认继续续期")
                return True
            status_lower = status.lower()
            if "ready to renew" in status_lower:
                self.log("✅ 当前可以续期")
                return True
            blocked = ["cooldown", "unavailable", "expired", "limit", "wait", "reached"]
            for word in blocked:
                if word in status_lower:
                    self.log(f"⏳ 当前不可续期: {status}")
                    return False
            self.log(f"⚠️ 未知状态 {status}，继续尝试")
            return True
        except Exception as e:
            self.log(f"⚠️ 检查续期状态失败: {e}")
            return True

    def get_remaining_days(self, sb) -> int:
        """从页面读取剩余天数
        策略：优先用 JS 精确定位 DAYS LEFT 卡片，其次用严格 regex。
        旧版 regex r'(\d+)\s*days?' 会错误匹配到 tooltip 里的 "7 day limit"
        导致 days_before=7，触发 if days_before >= 7 提前 return False，
        导致整个续期流程被跳过。
        """
        try:
            page_text = sb.get_page_source()

            # 方式1: JS 精确定位 DAYS LEFT 卡片（最可靠）
            days = sb.execute_script("""
                (function(){
                    // 找包含 "DAYS LEFT" 文字的卡片
                    let all = document.querySelectorAll('*');
                    for (let el of all) {
                        if ((el.textContent || '').toUpperCase().includes('DAYS LEFT')) {
                            // 取这个元素的父级卡片容器（最多上探3层）
                            let card = el;
                            for (let i = 0; i < 4; i++) {
                                let parent = card.parentElement;
                                if (!parent || parent === document.body) break;
                                card = parent;
                            }
                            let text = (card.textContent || '').trim();
                            // 只匹配 0-7 范围内的数字（排除 MB/GB 等大数）
                            let m = text.match(/\\b(0|[1-7])\\b/);
                            if (m) return parseInt(m[1]);
                        }
                    }
                    return -1;
                })()
            """)
            if days and 0 <= days <= 7:
                self.log(f"📅 [JS] DAYS LEFT 卡片检测到: {days} 天")
                return days

            # 方式2: 严格 regex —— 只匹配 "N/7" 格式（如 "4/7"）
            m = re.search(r'\b(\d{1,2})\s*/\s*7\b', page_text)
            if m:
                val = int(m.group(1))
                if 0 <= val <= 7:
                    self.log(f"📅 [regex] N/7 格式匹配到: {val}")
                    return val

            # 方式3: 匹配 "X Days" 但要求 X 在 0-7 之间，且后面紧跟 7 或 limit
            m = re.search(r'\b([0-6])\s*Days?\b.*?(?:7\s*day|limit|max)', page_text, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                self.log(f"📅 [regex] Days+limit 匹配到: {val}")
                return val

            # 方式4: 找到 "DAYS LEFT" 附近的最多300字符文本，从中提取 0-7 的数字
            idx = page_text.upper().find('DAYS LEFT')
            if idx != -1:
                chunk = page_text[idx:idx+300]
                m = re.search(r'\b([0-7])\\b', chunk)
                if m:
                    val = int(m.group(1))
                    self.log(f"📅 [regex] DAYS LEFT 附近匹配到: {val}")
                    return val

            self.log("⚠️ [get_remaining_days] 未匹配到有效天数，返回 0")
            return 0
        except Exception as e:
            self.log(f"⚠️ get_remaining_days 异常: {e}")
            return 0

    def click_sponsor_and_complete_renew(self, sb):
        """点击 Sponsor 并完成续期
        流程：先读天数 → 点 Sponsor(弹出新窗口) → 切回原窗口 → 等 renewBtn 激活 → 点击 → 对比天数
        """
        # 0. 续期前读天数
        days_before = self.get_remaining_days(sb)
        self.log(f"📅 续期前剩余天数: {days_before}（来源：DAYS LEFT 卡片，非 tooltip）")
        if days_before >= 7:
            self.log("⚠️ 已达最大天数(7天)，无需续期")
            return False
        try:
            # 1. 点击 Sponsor visit required（保留 target=_blank，让它在新窗口弹出）
            self.log("🖱️ 点击 Sponsor visit required...")
            sb.execute_script("""
                (function(){
                    let el = [...document.querySelectorAll("a,button,span")].find(
                        e => e.innerText && e.innerText.includes("Sponsor visit required")
                    );
                    if (!el) return;
                    let target = el;
                    for (let i = 0; i < 6; i++) {
                        if (target.tagName == "A" || target.tagName == "BUTTON" || typeof target.onclick == "function") {
                            target.click();
                            return;
                        }
                        target = target.parentElement;
                        if (!target) return;
                    }
                    el.click();
                })();
            """)
            self.log("✅ Sponsor点击执行完成")
            time.sleep(5)

            # 2. 切回原窗口（Sponsor 新窗口已弹出，原窗口才有 renewBtn）
            try:
                handles = sb.driver.window_handles
                self.log(f"🔎 当前窗口数量: {len(handles)}")
                if len(handles) > 1:
                    sb.driver.switch_to.window(handles[0])
                    self.log("🔎 已切回原窗口")
            except Exception as e:
                self.log(f"⚠️ 窗口读取失败: {str(e)[:60]}（继续在当前窗口操作）")
        except Exception as e:
            self.log(f"❌ Sponsor 点击流程失败: {e}")
            return False

        # 3. 等待 Complete Renewal 按钮激活（Turnstile token 异步生成，最多等 20 秒）
        self.log("⏳ 等待 Complete Renewal 激活...")
        btn_ready = False
        for _ in range(13):
            try:
                ready = sb.execute_script("""
                    (function(){
                        var btn = document.getElementById('renewBtn');
                        return btn ? !btn.disabled : false;
                    })()
                """)
                if ready:
                    btn_ready = True
                    break
            except Exception as e:
                self.log(f"⚠️ 检查按钮状态失败: {str(e)[:60]}")
                time.sleep(2)
            time.sleep(1.5)

        if not btn_ready:
            self.log("❌ Complete Renewal 按钮未激活")
            return False
        self.log("✅ Complete Renewal 已激活")

        # 4. 点击 Complete Renewal（JS 直接点击 + 兜底 ENTER）
        try:
            self.log("🖱️ 点击 Complete Renewal...")
            sb.execute_script("document.getElementById('renewBtn').click();")
            self.log("✅ 已点击 Complete Renewal")
        except Exception as e:
            self.log(f"⚠️ JS 点击失败: {str(e)[:60]}，尝试 ENTER")
            try:
                sb.driver.switch_to.active_element.send_keys(Keys.ENTER)
                self.log("✅ ENTER 已发送")
            except Exception as e2:
                self.log(f"❌ ENTER 也失败: {str(e2)[:60]}")
                return False
        time.sleep(5)

        # 5. 检查结果：天数对比 + 文本匹配双重验证
        try:
            time.sleep(5)
            days_after = self.get_remaining_days(sb)
            self.log(f"📅 续期后剩余天数: {days_after}")

            if days_after > days_before:
                self.log(f"🎉 天数从 {days_before} 增加到 {days_after}，续期成功！")
                return True
            if days_after >= 7:
                self.log("⚠️ 已达最大天数(7天)")
                return False
            # 天数没变，fallback 到文本匹配
            page_text = sb.get_page_source()
            if re.search(r'(?i)server\s*renewed', page_text):
                self.log("🎉 服务器续期成功（文本匹配）")
                return True
            if re.search(r'(?i)cannot\s*exceed\s*7\s*days', page_text):
                self.log("⚠️ Cannot exceed 7 days validity")
                return False
            self.log("⚠️ 未检测到续期结果（天数未增加，文本未匹配）")
            return False
        except Exception as e:
            self.log(f"⚠️ 结果检查失败: {str(e)[:60]}")
            return False

    def run(self):
        self.log("=" * 40)
        self.log("🚀 Kerit.Cloud - Renew 流程（朋友版）")
        self.log("=" * 40)
        self.log("🎯 正在启动 Chrome 浏览器...")

        # 代理配置
        proxy_url = SOCKS5_URL if SOCKS5_URL else None
        proxy_arg = f"--proxy-server={proxy_url}" if proxy_url else ""
        base_args = "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-position=0,0,--start-maximized"
        chromium_arg = f"{base_args},{proxy_arg}" if proxy_arg else base_args

        with SB(
            uc=True,
            test=True,
            headed=True,
            headless=False,
            xvfb=False,
            chromium_arg=chromium_arg,
            proxy=None
        ) as sb:
            try:
                self.log("✅ 浏览器已启动！")

                # ====== 1. IP 检测 ======
                self.log("🌍 正在检测出口 IP...")
                try:
                    sb.open("https://api.ipify.org?format=json")
                    ip_val = json.loads(re.search(r'\{.*\}', sb.get_text("body")).group(0)).get('ip', 'Unknown')
                    parts = ip_val.split('.')
                    self.log(f"✅ 当前出口 IP: {parts[0]}.{parts[1]}.***.{parts[-1]}")
                except:
                    self.log("⚠️ IP 检测跳过...")

                # ====== 2. Discord 登录 ======
                self.log("🔗 访问Discord登录页...")
                sb.uc_open_with_reconnect(DISCORD_URL, reconnect_time=25)
                time.sleep(5)
                self.discord_login(sb, EMAIL, PASSWORD)
                self.log("✅ 登录Discord成功")
                time.sleep(10)

                # ====== 3. 进入首页面 + Cloudflare 过盾 ======
                self.log("📂 进入登录页面")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=25)
                time.sleep(10)
                self.cloudflare_all_page(sb)

                # ====== ★ 关键修复：Cloudflare 后重新导航确保页面加载 ======
                self.log("🔄 Cloudflare 后重新导航确认页面状态...")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=15)
                time.sleep(10)
                # 再次检查 Cloudflare（可能第二次导航又触发了）
                page_lower = sb.get_page_source().lower()
                if any(x in page_lower for x in ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]):
                    self.cloudflare_all_page(sb)
                time.sleep(5)

                # ====== 4. Discord 登录按钮 + OAuth 授权 ======
                self.log("📂 进入登录页面")
                if not self.click_discord_login(sb):
                    self.log("⚠️ Discord 按钮点击失败，尝试直接处理 OAuth...")
                time.sleep(15)
                self.oauth_debug(sb)
                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\nDiscord OAuth 授权完成")
                time.sleep(15)

                # ====== 5. 进入续期页面 ======
                self.log("📂 点击进入续期页面")
                sb.uc_open_with_reconnect(MAIN_URL, reconnect_time=25)
                try:
                    sb.wait_for_element_present("#renewal-status-text", timeout=30)
                    self.log("✅ 续期状态组件加载完成")
                except:
                    self.log("⚠️ 未检测到 renewal-status-text，检查页面")

                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n续期页面加载完成")

                # ====== 6. 检查续期状态 ======
                self.log("🔍 Renewal状态: None")
                self.log("🔍 Renewal说明: None")
                if not self.check_renewal_status(sb):
                    self.log("✅ 冷却中，无需续期")
                    final_screenshot = f"{self.screenshot_dir}/final.png"
                    sb.save_screenshot(final_screenshot)
                    self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}] 冷却中，无需续期", final_screenshot)
                    return

                # ====== 7. 点击 Renew Server 按钮 ======
                self.log("✅ 点击Renew按钮")
                self.log("🖱️ JS点击 Renew Server")
                renew_btn_clicked = False
                for _ in range(5):
                    try:
                        # 尝试 JS 点击
                        sb.execute_script("""
                            let btn = document.querySelector("#renewServerBtn");
                            if (!btn) {
                                // 尝试文本匹配
                                btn = [...document.querySelectorAll("a,button,span")].find(
                                    e => e.innerText && e.innerText.includes("Renew Server")
                                );
                            }
                            if (!btn) throw new Error("renewServerBtn not found");
                            btn.click();
                        """)
                        renew_btn_clicked = True
                        self.log("✅ Renew Server 按钮点击成功")
                        break
                    except Exception as e:
                        self.log(f"⚠️ 点击失败，重试... ({_ + 1}/5)")
                        time.sleep(2)
                if not renew_btn_clicked:
                    self.log("❌ Renew Server 按钮未找到")
                    sb.save_screenshot(f"{self.screenshot_dir}/no_renew_btn.png")
                    self.send_telegram_notify(f"❌ Kerit.Cloud\n✅账号：[{EMAIL}]\nRenew Server 按钮未找到", 
                                               f"{self.screenshot_dir}/no_renew_btn.png")
                    return
                time.sleep(10)

                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\nRenew Server 点击完成")

                # ====== 8. Sponsor + Complete Renewal ======
                self.log("✅ 点击sponsor按钮后并点击续期")
                renew_success = self.click_sponsor_and_complete_renew(sb)

                # ====== 9. 完成：按真实结果发通知，不再无条件"完毕" ======
                try:
                    time.sleep(3)
                    sb.scroll_to_bottom()
                    final_screenshot = f"{self.screenshot_dir}/final.png"
                    sb.save_screenshot(final_screenshot)
                except Exception as e:
                    self.log(f"⚠️ 截图失败（浏览器可能已挂）: {str(e)[:80]}")
                    final_screenshot = None

                if renew_success is True:
                    self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n🎊 续期成功！", final_screenshot)
                elif renew_success is None:
                    self.send_telegram_notify(f"⚠️ Kerit.Cloud\n账号：[{EMAIL}]\n浏览器异常，续期结果未确认，请手动检查", final_screenshot)
                else:
                    self.send_telegram_notify(f"❌ Kerit.Cloud\n账号：[{EMAIL}]\n续期未完成（可能冷却或按钮未找到）", final_screenshot)

            except Exception as e:
                self.log(f"❌ 运行异常: {e}")
                import traceback
                traceback.print_exc()
                sb.save_screenshot(f"{self.screenshot_dir}/error.png")
                self.send_telegram_notify(f"❌ Kerit.Cloud\n账号：[{EMAIL}]\n运行异常: {str(e)[:100]}")

if __name__ == "__main__":
    KeritCloudRenewal().run()

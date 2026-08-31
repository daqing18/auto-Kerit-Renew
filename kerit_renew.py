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
_node_link = os.getenv("NODE_LINK", "")
_proxy_env = os.getenv("PROXY", os.getenv("PROXY_SERVER", ""))
if _proxy_env and re.match(r'^(socks5|socks4|http|https)://', _proxy_env):
    SOCKS5_URL = _proxy_env
elif _node_link and re.match(r'^(socks5|socks4|http|https)://', _node_link):
    SOCKS5_URL = _node_link
else:
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
        self.log("🔍 等待 Discord 登录按钮...")
        for attempt in range(2):
            try:
                sb.wait_for_element_visible('a[href="/auth/discord"]', timeout=15)
                sb.click('a[href="/auth/discord"]')
                self.log("✅ Discord 登录按钮点击成功")
                return True
            except Exception:
                pass

            try:
                for e in sb.find_elements("a, span, button"):
                    try:
                        text = (e.text or "").strip()
                        if "Discord" in text or "discord" in text.lower():
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

            self.log("🔄 Discord 按钮未找到，重新导航页面...")
            try:
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=15)
                time.sleep(8)
                self.cloudflare_all_page(sb)
                time.sleep(5)
            except Exception:
                pass

        self.log("❌ Discord 登录按钮未能点击")
        return False

    def oauth_debug(self, sb):
        self.log("🔐 OAuth 页面分析开始")
        time.sleep(3)
        for i in range(2):
            self.log(f"🔍 分析 {i+1}/2")
            time.sleep(2)
            try:
                buttons = sb.find_elements("button")
                self.log(f"找到按钮数量: {len(buttons)}")
                for btn in buttons:
                    try:
                        text = (btn.text or "").strip()
                        self.log(f"按钮: {repr(text)}")
                        if "继续滚动" in text or "continue" in text.lower():
                            self.log("🟡 发现继续滚动按钮")
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
                            sb.execute_script("document.querySelectorAll('button')[1].click();")
                            self.log("✅ 已点击继续滚动")
                            time.sleep(5)
                            url = sb.get_current_url()
                            self.log(f"当前URL:{url}")
                            if "kerit.cloud" in url:
                                self.log("✅ OAuth完成")
                                return True
                            break
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

            try:
                url = sb.get_current_url()
                self.log(f"当前URL:{url}")
                if "kerit.cloud/session" in url or "kerit.cloud" in url:
                    self.log("✅ OAuth完成")
                    return True
            except Exception:
                pass

        self.log("❌ OAuth失败")
        return False

    def check_renewal_status(self, sb):
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
                for i in range(5):
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
        try:
            page_text = sb.get_page_source()
            days = sb.execute_script("""
                (function(){
                    let all = document.querySelectorAll('*');
                    let best = null;
                    for (let el of all) {
                        let t = (el.innerText || el.textContent || '').trim();
                        if (!t || t.length > 30) continue;
                        let m = t.match(/^\\s*(\\d{1,2})\\s*days?\\s*$/i);
                        if (m) {
                            let v = parseInt(m[1]);
                            if (v >= 0 && v <= 7) {
                                if (!best || t.length < best.len) best = { v: v, len: t.length, text: t };
                            }
                        }
                    }
                    if (best) return best.v;
                    let best2 = null;
                    for (let el of all) {
                        let t = (el.textContent || '').toUpperCase();
                        if (t.includes('DAYS LEFT') && t.length < 300) {
                            if (!best2 || t.length < best2.len) best2 = { el, len: t.length };
                        }
                    }
                    if (best2) {
                        let node = best2.el;
                        for (let i = 0; i < 4; i++) {
                            let text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ');
                            let m = text.match(/DAYS\\s*LEFT[^\\d]{0,15}(\\d{1,2})/i);
                            if (m) { let v = parseInt(m[1]); if (v >= 0 && v <= 7) return v; }
                            node = node.parentElement;
                            if (!node || node === document.body) break;
                        }
                    }
                    return -1;
                })()
            """)
            if days and 0 <= days <= 7:
                self.log(f"📅 [JS] DAYS LEFT 卡片检测到: {days} 天")
                return days

            m = re.search(r'\b(\d{1,2})\s*/\s*7\b', page_text)
            if m:
                val = int(m.group(1))
                if 0 <= val <= 7:
                    self.log(f"📅 [regex] N/7 格式匹配到: {val}")
                    return val

            m = re.search(r'\b([0-6])\s*Days?\b.*?(?:7\s*day|limit|max)', page_text, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                self.log(f"📅 [regex] Days+limit 匹配到: {val}")
                return val

            idx = page_text.upper().find('DAYS LEFT')
            if idx != -1:
                chunk = page_text[idx:idx+300]
                m = re.search(r'\b([0-7])\b', chunk)
                if m:
                    val = int(m.group(1))
                    self.log(f"📅 [regex] DAYS LEFT 附近匹配到: {val}")
                    return val

            self.log("⚠️ [get_remaining_days] 未匹配到有效天数，返回 0")
            return 0
        except Exception as e:
            self.log(f"⚠️ get_remaining_days 异常: {e}")
            return 0

    def _send_check6_debug(self, sb, days_before):
        try:
            shot = f"{self.screenshot_dir}/check6_fail.png"
            try:
                sb.save_screenshot(shot)
            except Exception:
                shot = None
            diag = None
            try:
                diag = sb.execute_script("""
                    (function(){
                        var out = {url: location.href, iframes: document.querySelectorAll('iframe').length};
                        var texts = [];
                        var all = document.querySelectorAll('body *');
                        for (var i = 0; i < all.length && texts.length < 25; i++) {
                            var el = all[i];
                            if (el.getClientRects().length === 0) continue;
                            var t = (el.textContent||'').trim();
                            if (t.length > 1 && t.length < 60 && el.children.length === 0) texts.push(t);
                        }
                        out.texts = texts;
                        return out;
                    })()
                """)
            except Exception as e:
                self.log(f"   🔬 现场分析失败: {str(e)[:60]}")
            msg = (f"⚠️ Kerit 检查⑥失败（未找到 Complete Renewal）\n"
                   f"URL: {(diag or {}).get('url','?')}\n"
                   f"iframe数: {(diag or {}).get('iframes','?')}\n"
                   f"剩余天数: {days_before}")
            if diag and diag.get('texts'):
                msg += "\n可见文本: " + " | ".join(diag['texts'][:15])
            self.send_telegram_notify(msg, shot)
        except Exception as e:
            self.log(f"   🔬 诊断/截图失败: {str(e)[:60]}")

    def click_sponsor_and_complete_renew(self, sb):
        days_before = self.get_remaining_days(sb)
        self.log(f"📅 续期前剩余天数: {days_before}（来源：DAYS LEFT 卡片，非 tooltip）")
        if days_before >= 7:
            self.log("⚠️ 已达最大天数(7天)，无需续期")
            return (False, days_before, days_before)

        try:
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
        except Exception as e:
            self.log(f"❌ 检查①失败（Sponsor 点击失败）: {e}，关闭本次续期")
            return (False, days_before, -1)

        self.log("🩺 检查②：浏览器健康检查（含 CDP 自动重连）...")
        browser_ok = False
        for hc in range(5):
            try:
                handles = list(sb.driver.window_handles)
                browser_ok = True
                self.log(f"   ✅ 浏览器正常，当前窗口数: {len(handles)}")
                break
            except Exception as e:
                self.log(f"   ⚠️ 浏览器/CDP 未就绪 {hc+1}/5: {str(e)[:60]}，尝试重连 CDP...")
                try:
                    if hasattr(sb.driver, "connect"):
                        sb.driver.connect()
                    time.sleep(2)
                except Exception as ce:
                    self.log(f"   ⚠️ CDP 重连失败: {str(ce)[:60]}")
                    time.sleep(3)
        if not browser_ok:
            self.log("❌ 检查②失败（浏览器连接异常/疑似崩溃，CDP 重连无效），关闭本次续期")
            return (False, days_before, -1)

        self.log("⏳ 检查③：等待 Sponsor 页面加载完成...")
        sponsor_loaded = False
        for _ in range(3):
            try:
                for h in list(sb.driver.window_handles):
                    try:
                        sb.driver.switch_to.window(h)
                        if "billing.kerit.cloud" not in sb.driver.current_url:
                            st = sb.execute_script("return document.readyState;")
                            if st == "complete":
                                sponsor_loaded = True
                                break
                    except Exception:
                        continue
                if sponsor_loaded:
                    break
            except Exception as e:
                self.log(f"   ⚠️ 等待 Sponsor 加载重试: {str(e)[:50]}")
            time.sleep(2)
            
        if not sponsor_loaded:
            self.log("❌ 检查③失败（Sponsor 页面未加载完成），关闭本次续期")
            return (False, days_before, -1)
            
        self.log("✅ 检查③通过：Sponsor 页面已加载完成")

        self.log("⏳ 模拟真实浏览，在 Sponsor 页面停留 18 秒以完成有效验证...")
        time.sleep(18)

        self.log("🔎 检查④：关闭 Sponsor 窗口并验证回落...")
        back_ok = False
        try:
            closed_any = False
            for h in list(sb.driver.window_handles):
                try:
                    sb.driver.switch_to.window(h)
                    if "billing.kerit.cloud" not in sb.driver.current_url:
                        sb.driver.close()
                        closed_any = True
                except Exception:
                    continue
            for h in list(sb.driver.window_handles):
                try:
                    sb.driver.switch_to.window(h)
                    if "billing.kerit.cloud" in sb.driver.current_url:
                        back_ok = True
                        break
                except Exception:
                    continue
            if closed_any:
                self.log("   ✅ 已关闭 Sponsor 新窗口")
        except Exception as e:
            self.log(f"   ⚠️ 关闭 Sponsor 窗口异常: {str(e)[:60]}")
        if not back_ok:
            self.log("❌ 检查④失败（未回落到续期页），关闭本次续期")
            return (False, days_before, -1)
        self.log(f"✅ 检查④通过：已回落续期页 {sb.driver.current_url}")

        try:
            cur_url = sb.driver.current_url
            if "billing.kerit.cloud" not in cur_url:
                self.log(f"❌ 检查⑤失败（当前不在续期页: {cur_url}），关闭本次续期")
                return (False, days_before, -1)
        except Exception as e:
            self.log(f"❌ 检查⑤异常: {str(e)[:60]}，关闭本次续期")
            return (False, days_before, -1)
        self.log("✅ 检查⑤通过：当前在续期页")

        # ===== 检查⑥ & ⑦：使用原生 CSS 选择器定位并点击 Complete Renewal =====
        self.log("⏳ 检查⑥：定位并等待 Complete Renewal 按钮渲染...")
        try:
            # CSS 原生多重匹配，包含 button 和 input[value="..."]
            target_selector = 'button:icontains("Complete Renewal"), input[value*="Complete Renewal" i]'
            sb.wait_for_element_visible(target_selector, timeout=20)
            
            final_url = sb.driver.current_url
            if "billing.kerit.cloud" not in (final_url or ""):
                self.log(f"❌ 检查⑦失败（不在续期页: {final_url}），关闭本次续期")
                return (False, days_before, -1)
            self.log("✅ 检查⑦通过：全部就绪，开始点击 Complete Renewal")

            self.log("🖱️ 点击 Complete Renewal...")
            sb.click(target_selector)
            self.log("✅ 已点击 Complete Renewal")
        except Exception as e:
            self.log(f"❌ 未找到 Complete Renewal 按钮，关闭本次续期")
            self._send_check6_debug(sb, days_before)
            return (False, days_before, -1)

        time.sleep(8)

        try:
            days_after = self.get_remaining_days(sb)
            self.log(f"📅 续期后剩余天数: {days_after}")
            if days_after > days_before:
                self.log(f"🎉 天数从 {days_before} 增加到 {days_after}，续期成功！")
                return (True, days_before, days_after)
            if days_after >= 7:
                self.log("⚠️ 已达最大天数(7天)")
                return (True, days_before, days_after)
            try:
                sb.reload_page()
                time.sleep(6)
                days_after2 = self.get_remaining_days(sb)
                self.log(f"📅 刷新后剩余天数: {days_after2}")
                if days_after2 > days_before:
                    self.log(f"🎉 天数从 {days_before} 增加到 {days_after2}，续期成功！")
                    return (True, days_before, days_after2)
                if days_after2 >= 7:
                    self.log("⚠️ 已达最大天数(7天)")
                    return (True, days_before, days_after2)
            except Exception as e:
                self.log(f"⚠️ 刷新重读失败: {str(e)[:60]}")
            self.log("❌ 续期未生效：天数未增加（Sponsor 已走完但续期未生效）")
            return (False, days_before, days_after)
        except Exception as e:
            self.log(f"⚠️ 结果检查失败: {str(e)[:60]}")
            return (False, days_before, -1)

    def run(self):
        self.log("=" * 40)
        self.log("🚀 Kerit.Cloud - Renew 流程（朋友版）")
        self.log("=" * 40)
        self.log("🎯 正在启动 Chrome 浏览器...")

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
                self.log("🌍 正在检测出口 IP...")
                try:
                    sb.open("https://api.ipify.org?format=json")
                    ip_val = json.loads(re.search(r'\{.*\}', sb.get_text("body")).group(0)).get('ip', 'Unknown')
                    parts = ip_val.split('.')
                    self.log(f"✅ 当前出口 IP: {parts[0]}.{parts[1]}.***.{parts[-1]}")
                except:
                    self.log("⚠️ IP 检测跳过...")

                self.log("🔗 访问Discord登录页...")
                sb.uc_open_with_reconnect(DISCORD_URL, reconnect_time=25)
                time.sleep(5)
                self.discord_login(sb, EMAIL, PASSWORD)
                self.log("✅ 登录Discord成功")
                time.sleep(10)

                self.log("📂 进入登录页面")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=25)
                time.sleep(10)
                self.cloudflare_all_page(sb)

                self.log("🔄 Cloudflare 后重新导航确认页面状态...")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=15)
                time.sleep(10)
                page_lower = sb.get_page_source().lower()
                if any(x in page_lower for x in ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]):
                    self.cloudflare_all_page(sb)
                time.sleep(5)

                self.log("📂 进入登录页面")
                if not self.click_discord_login(sb):
                    self.log("⚠️ Discord 按钮点击失败，尝试直接处理 OAuth...")
                time.sleep(15)
                self.oauth_debug(sb)
                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\nDiscord OAuth 授权完成")
                time.sleep(15)

                self.log("📂 点击进入续期页面")
                sb.uc_open_with_reconnect(MAIN_URL, reconnect_time=25)
                try:
                    sb.wait_for_element_present("#renewal-status-text", timeout=30)
                    self.log("✅ 续期状态组件加载完成")
                except:
                    self.log("⚠️ 未检测到 renewal-status-text，检查页面")

                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n续期页面加载完成")

                self.log("🔍 Renewal状态: None")
                self.log("🔍 Renewal说明: None")
                if not self.check_renewal_status(sb):
                    self.log("✅ 冷却中，无需续期")
                    final_screenshot = f"{self.screenshot_dir}/final.png"
                    sb.save_screenshot(final_screenshot)
                    cur_days = self.get_remaining_days(sb)
                    self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n⏳ 冷却中，无需续期\n📅 当前剩余天数: {cur_days}", final_screenshot)
                    return

                self.log("✅ 点击Renew按钮")
                self.log("🖱️ JS点击 Renew Server")
                renew_btn_clicked = False
                for _ in range(3):
                    try:
                        sb.execute_script("""
                            let btn = document.querySelector("#renewServerBtn");
                            if (!btn) {
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
                        self.log(f"⚠️ 点击失败，重试... ({_ + 1}/3)")
                        time.sleep(2)
                if not renew_btn_clicked:
                    self.log("❌ Renew Server 按钮未找到")
                    sb.save_screenshot(f"{self.screenshot_dir}/no_renew_btn.png")
                    cur_days = self.get_remaining_days(sb)
                    self.send_telegram_notify(f"❌ Kerit.Cloud\n✅账号：[{EMAIL}]\nRenew Server 按钮未找到\n📅 当前剩余天数: {cur_days}", 
                                               f"{self.screenshot_dir}/no_renew_btn.png")
                    return
                time.sleep(10)

                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\nRenew Server 点击完成")

                self.log("✅ 点击sponsor按钮后并点击续期")
                renew_result = self.click_sponsor_and_complete_renew(sb)
                renew_success = renew_result[0]
                renew_days_before = renew_result[1]
                renew_days_after = renew_result[2]

                try:
                    time.sleep(3)
                    sb.scroll_to_bottom()
                    final_screenshot = f"{self.screenshot_dir}/final.png"
                    sb.save_screenshot(final_screenshot)
                except Exception as e:
                    self.log(f"⚠️ 截图失败（浏览器可能已挂）: {str(e)[:80]}")
                    final_screenshot = None

                if renew_success is True:
                    if renew_days_after >= 0:
                        self.send_telegram_notify(
                            f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n🎊 续期成功！\n"
                            f"📅 续期前剩余天数: {renew_days_before}，续期后剩余天数: {renew_days_after}",
                            final_screenshot)
                    else:
                        self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{EMAIL}]\n🎊 续期成功！", final_screenshot)
                elif renew_success is None:
                    self.send_telegram_notify(f"⚠️ Kerit.Cloud\n账号：[{EMAIL}]\n浏览器异常，续期结果未确认，请手动检查", final_screenshot)
                else:
                    if renew_days_after >= 0:
                        self.send_telegram_notify(
                            f"❌ Kerit.Cloud\n账号：[{EMAIL}]\n续期未完成\n"
                            f"📅 续期前剩余天数: {renew_days_before}，续期后剩余天数: {renew_days_after}",
                            final_screenshot)
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

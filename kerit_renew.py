import time
import os
import json
import re
import random
import requests

# 智能环境配置：仅在未设置时才应用默认值
# 这样兼容 GitHub Actions 的 xvfb-run (会自动设置 DISPLAY) 和 Docker 环境
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":1"
    
if "XAUTHORITY" not in os.environ:
    # 仅当路径存在时才设置，避免在 GitHub Runner (home/runner) 中报错
    if os.path.exists("/home/headless/.Xauthority"):
        os.environ["XAUTHORITY"] = "/home/headless/.Xauthority"

print(f"[DEBUG] Env DISPLAY: {os.environ.get('DISPLAY')}")
print(f"[DEBUG] Env XAUTHORITY: {os.environ.get('XAUTHORITY')}")

from seleniumbase import SB
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# ================= 配置区域 =================
EMAIL = os.getenv("EMAIL")  # discord邮箱
PASSWORD = os.getenv("PASSWORD")  # discord密码
TG_TOKEN = os.getenv("TG_TOKEN")  # tg通知token
TG_CHAT_ID = os.getenv("TG_CHAT_ID")  # tg通知chat_id

# 自动挂载 sing-box 代理
SOCKS5_URL = os.getenv("PROXY_SERVER", "")
if not SOCKS5_URL and os.getenv("NODE_LINK"):
    SOCKS5_URL = "socks5://127.0.0.1:1080" # setup_proxy.sh 默认的本地 socks5 端口

# 目标 URL
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

    def human_wait(self, min_s=6, max_s=10):
        """随机模拟人类等待时间"""
        time.sleep(random.uniform(min_s, max_s))

    def move_mouse_human(self, sb):
        """模拟人类鼠标晃动预热"""
        try:
            for _ in range(3):
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                sb.slow_click(f"body", force=True)
                time.sleep(random.uniform(0.5, 1.2))
        except: pass

    def send_telegram_notify(self, message, photo_path=None):
        """发送 Telegram 通知 (带图片)"""
        if not TG_TOKEN or not TG_CHAT_ID:
            self.log("⚠️ 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过推送。")
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

    def discord_login(self, sb, EMAIL, PASSWORD):
        self.log("✏️ 输入账号密码")
        sb.fill('input[name="email"]', EMAIL)
        sb.fill('input[name="password"]', PASSWORD)
        self.log("📤 提交登录")
        sb.click('button[type="submit"]')
        time.sleep(10)

    # ======================
    # OAuth
    # ======================
    def oauth_debug(self, sb):
        self.log("🔐 OAuth 页面分析开始")
        for i in range(40):
            self.log(f"🔍 分析 {i+1}/40")
            time.sleep(2)

            try:
                self.log("🔍 查找 Discord 授权按钮")
                buttons = sb.find_elements("button")
                self.log(f"找到按钮数量: {len(buttons)}")

                for btn in buttons:
                    try:
                        text = (btn.text or "").strip()
                        self.log(f"按钮: {repr(text)}")

                        if "继续滚动" in text:
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
                            sb.execute_script('document.querySelectorAll("button")[1].click();')
                            self.log("✅ 已点击继续滚动")
                            time.sleep(5)
                            break

                        if "授权" in text or text == "Authorize" or text == "Authorise":
                            self.log("🟢 找到授权按钮")
                            sb.execute_script('document.querySelectorAll("button")[1].click();')
                            self.log("✅ OAuth授权点击完成")
                            time.sleep(8)
                            break

                    except Exception as e:
                        self.log(f"按钮处理错误: {e}")

            except Exception as e:
                self.log(f"OAuth按钮检测失败: {e}")

            try:
                url = sb.get_current_url()
                self.log(f"当前URL:{url}")
                # 修复误判：如果在根目录登录页，不能算作授权完成
                if "kerit.cloud" in url and "login" not in url.lower() and url.strip("/") != "https://billing.kerit.cloud":
                    self.log("✅ OAuth完成 (已成功进入面板后台)")
                    return True
            except Exception:
                pass

        self.log("❌ OAuth失败")
        return False

    def click_discord_login(self, sb):
        try:
            self.log("🔍 等待 Discord 登录按钮...")
            # 扩大选择器范围，适配前端可能的变化
            selectors = [
                'a[href="/auth/discord"]',
                '//button[contains(., "Continue with Discord")]',
                '//span[contains(., "Continue with Discord")]',
                '//a[contains(., "Discord")]'
            ]
            clicked = False
            for sel in selectors:
                try:
                    if sb.is_element_visible(sel):
                        sb.click(sel)
                        clicked = True
                        self.log(f"✅ Discord 登录按钮点击成功 ({sel})")
                        break
                except:
                    continue
            
            if not clicked:
                # 尝试用 JS 点击包含 discord 字符的按钮
                sb.execute_script("""
                    let btns = document.querySelectorAll('button, a');
                    for (let b of btns) {
                        if ((b.innerText || '').toLowerCase().includes('discord')) {
                            b.click();
                            return;
                        }
                    }
                """)
                self.log("✅ 尝试使用 JS 强制点击 Discord 按钮")
                
            return True
        except Exception as e:
            self.log(f"❌ Discord 登录点击失败: {e}")
            return False

    def click_sponsor_and_complete_renew(self, sb):
        try:
            self.log("🖱️ 点击 Sponsor visit required...")
            sb.execute_script("""
            (function(){
                let el=[...document.querySelectorAll("a,button,span")].find(
                    e => e.innerText && e.innerText.includes("Sponsor visit required")
                );
                if(!el) return;
                let target=el;
                for(let i=0;i<6;i++){
                    if(target.tagName=="A" || target.tagName=="BUTTON" || typeof target.onclick=="function"){
                        if(target.tagName=="A"){ target.removeAttribute("target"); }
                        target.click();
                        return;
                    }
                    target=target.parentElement;
                    if(!target) return;
                }
                el.click();
            })();
            """)
            self.log("✅ Sponsor点击执行完成")
            time.sleep(3)

            handles = sb.driver.window_handles
            self.log(f"🔎 当前窗口数量: {len(handles)}")
            for i,h in enumerate(handles):
                self.log(f"🔎 Window {i}: {h}")

            self.log("🔎 检查 Turnstile 状态")
            state = sb.execute_script("""
            return {
                token: typeof renewalState !== "undefined" ? renewalState.turnstileToken : "NO",
                hidden: document.querySelector("#cf-chl-widget-34adu_response")?.value || ""
            };
            """)
            self.log(f"🔎 Turnstile状态: {state}")
            self.log("⏳等待 Complete Renewal 激活...")
            time.sleep(3)

            self.log("🔍 查找 Complete Renewal 按钮...")
            renew_btn = sb.find_element("#renewBtn")
            self.log("✅ 找到 Complete Renewal")

            sb.execute_script("""
                arguments[0].scrollIntoView({block:'center'});
                arguments[0].focus();
            """, renew_btn)
            time.sleep(2)

            active = sb.execute_script("""
            let el=document.activeElement;
            return {
                tag:el.tagName, id:el.id, text:el.innerText,
                html: el.outerHTML.substring(0,300)
            };
            """)
            self.log(f"🔎 当前焦点: {active}")
            time.sleep(2)

            self.log("↩️ 发送 ENTER")
            sb.driver.switch_to.active_element.send_keys(Keys.ENTER)
            self.log("✅ ENTER发送完成")

            time.sleep(5)
            renew_result = sb.execute_script("""
            return {
                success: document.body.innerText.includes("Server renewed successfully"),
                error: document.body.innerText.includes("Cannot exceed 7 days validity")
            };
            """)
            self.log(f"🔎 Renewal结果: {renew_result}")
            if renew_result["success"]:
                self.log("🎉 服务器续期成功")
                return True
            if renew_result["error"]:
                self.log("⚠️ Cannot exceed 7 days validity")
                return False
            self.log("⚠️ 未检测到续期结果")
            return False
        except Exception as e:
            self.log(f"❌ Renewal流程失败: {e}")
            return False

    def cloudflare_all_page(self, sb):
        self.log("⏳ 处理 Cloudflare 挑战...")
        cf_indicators = ["verify you are human", "确认您是真人", "troubleshoot",
                         "just a moment", "checking your browser", "performing security"]
        
        for attempt in range(8):
            page_lower = sb.get_page_source().lower()
            if not any(x in page_lower for x in cf_indicators):
                self.log("✅ Cloudflare 验证已通过")
                return True
            
            self.log(f"🛡️ 检测到 CF 盾，第 {attempt+1}/8 次尝试...")
            
            # 策略1：直接在 iframe 里找 checkbox 点击
            try:
                frames = sb.find_elements("iframe")
                clicked = False
                for frame in frames:
                    try:
                        sb.driver.switch_to.frame(frame)
                        cb = sb.find_element("input[type='checkbox']")
                        cb.click()
                        clicked = True
                        sb.driver.switch_to.default_content()
                        self.log("🖱️ 直接点击 iframe checkbox")
                        break
                    except Exception:
                        try:
                            sb.driver.switch_to.default_content()
                        except:
                            pass
                if not clicked:
                    try:
                        sb.uc_gui_click_captcha()
                    except:
                        pass
                time.sleep(15)
                continue
            except Exception:
                pass
            
            try:
                sb.uc_gui_click_captcha()
            except:
                pass
            time.sleep(12)
        
        # 策略2：reconnect 刷新
        for attempt in range(3):
            self.log(f"🔄 reconnect 尝试 {attempt+1}/3...")
            try:
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=30)
                time.sleep(20)
                page_lower = sb.get_page_source().lower()
                if not any(x in page_lower for x in cf_indicators):
                    self.log("✅ reconnect 通过")
                    return True
            except:
                time.sleep(3)
        
        self.log("❌ Cloudflare 验证超时失败")
        return False

    def check_renewal_status(self, sb):
        try:
            status = sb.execute_script("""
            (function(){
                let el=document.querySelector('#renewal-status-text');
                return el ? (el.textContent || "").trim() : "";
            })();
            """)
            subtext = sb.execute_script("""
            (function(){
                let el=document.querySelector('#renewal-status-subtext');
                return el ? (el.textContent || "").trim() : "";
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
        
    def run(self):
        self.log("=" * 40)
        self.log("🚀 Kerit.Cloud - Renew流程 (GitHub Actions 版)")
        self.log("=" * 40)
        
        if not EMAIL or not PASSWORD:
            self.log("❌ 严重错误: 未检测到 EMAIL 或 PASSWORD 环境变量，请在 GitHub Secrets 中配置！")
            return

        self.log(f"🎯 正在启动 Chrome 浏览器... (代理: {SOCKS5_URL if SOCKS5_URL else '未配置直连'})")
        
        with SB(
            uc=True,            
            test=True, 
            headed=True,        
            headless=False,     
            xvfb=False,         
            chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-position=0,0,--start-maximized",
            proxy=SOCKS5_URL if SOCKS5_URL else None
        ) as sb:
            try:
                self.log("✅ 浏览器已启动！")
                self.log("🔗 访问Discord登录页...")
                sb.uc_open_with_reconnect(DISCORD_URL, reconnect_time=25)
                time.sleep(5)
                self.discord_login(sb, EMAIL, PASSWORD)
                self.log("✅ 登录Discord成功")
                time.sleep(10)

                self.log("📂 进入登录页面")
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=25)
                time.sleep(10)
                
                # 如果 CF 没过，直接停止，不要盲目往下执行
                if not self.cloudflare_all_page(sb):
                    self.log("❌ 无法绕过 Cloudflare，停止续期流程。大概率是代理节点IP质量差被拦截。")
                    sb.save_screenshot(f"{self.screenshot_dir}/cf_failed.png")
                    self.send_telegram_notify("❌ Cloudflare 拦截，续期失败", f"{self.screenshot_dir}/cf_failed.png")
                    return
                              
                self.log("📂 授权登录页面")
                self.click_discord_login(sb)
                time.sleep(15)
                self.oauth_debug(sb)
                time.sleep(15)

                self.log("📂 点击进入续期页面")
                sb.uc_open_with_reconnect(MAIN_URL, reconnect_time=25)
                try:
                    sb.wait_for_element_present("#renewal-status-text", timeout=20)
                    self.log("✅ 续期状态组件加载完成")
                except:
                    self.log("⚠️ 未检测到 renewal-status-text，检查页面")

                if not self.check_renewal_status(sb):
                    self.log("✅冷却中,无需续期")
                    final_screenshot = f"{self.screenshot_dir}/final.png"
                    sb.save_screenshot(final_screenshot)
                    mask_mail = EMAIL[:3] + "***" + EMAIL[EMAIL.find("@"):]
                    self.send_telegram_notify(f"🎉Kerit.Cloud\n✅账号：[{mask_mail}] 冷却中,无需续期", final_screenshot)
                    return

                self.log("✅ 点击Renew按钮")
                self.log("🖱️ JS点击 Renew Server")
                sb.execute_script("""
                let btn = document.querySelector("#renewServerBtn");
                if (!btn) {
                    throw new Error("renewServerBtn not found");
                }
                btn.click();
                """)
                time.sleep(10)

                self.log("✅ 点击sponsor按钮后并点击续期")
                renew_success = self.click_sponsor_and_complete_renew(sb)
                time.sleep(3)
                sb.scroll_to_bottom()
                final_screenshot = f"{self.screenshot_dir}/final.png"
                sb.save_screenshot(final_screenshot)
                mask_mail = EMAIL[:3] + "***" + EMAIL[EMAIL.find("@"):]
                self.send_telegram_notify(f"🎉 Kerit.Cloud\n✅账号：[{mask_mail}]\n续期流程完毕", final_screenshot)
            
            except Exception as e:
                self.log(f"❌ 运行异常: {e}")
                import traceback
                traceback.print_exc()
                sb.save_screenshot(f"{self.screenshot_dir}/error.png")

if __name__ == "__main__":
    KeritCloudRenewal().run()

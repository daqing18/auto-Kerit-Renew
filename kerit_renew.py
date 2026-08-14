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
# 与 katabump 脚本保持一致：优先用 HTTP 代理端口 (1081)，CF 检测对 SOCKS5 更敏感
SOCKS5_URL = os.getenv("PROXY_SERVER", "")
if not SOCKS5_URL and os.getenv("NODE_LINK"):
    import socket
    def _port_open(port):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return True
        except Exception:
            return False
    if _port_open(1081):
        SOCKS5_URL = "http://127.0.0.1:1081"  # 与 katabump 一致
    else:
        SOCKS5_URL = "socks5://127.0.0.1:1080"  # setup_proxy.sh 默认的本地 socks5 端口

# 目标 URL
DISCORD_URL = "https://discord.com/login"
LOGIN_URL = "https://billing.kerit.cloud"
MAIN_URL = "https://billing.kerit.cloud/free_panel"
# ===========================================

# ============================================================
# CF Turnstile 过盾逻辑（katabump 风格）
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

_TURNSTILE_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_TURNSTILE_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""


def check_token(sb) -> bool:
    """检查 Turnstile token 是否已填充"""
    try:
        return sb.execute_script(_TURNSTILE_SOLVED_JS)
    except Exception:
        return False


def diagnose_turnstile(sb, log_prefix=""):
    """诊断 Turnstile 页面状态"""
    try:
        diag = sb.execute_script("""
        (function(){
            var result = {};
            // 检查 cf-turnstile-response input
            var ts_input = document.querySelector('input[name="cf-turnstile-response"]');
            result.has_turnstile_input = !!ts_input;
            if (ts_input) {
                result.turnstile_value_len = (ts_input.value || '').length;
            }
            // 检查所有 iframe
            var frames = document.querySelectorAll('iframe');
            result.frame_count = frames.length;
            result.cf_frames = [];
            frames.forEach(function(f, i) {
                var src = f.src || '';
                if (src.includes('challenges.cloudflare.com') || src.includes('turnstile')) {
                    var rect = f.getBoundingClientRect();
                    result.cf_frames.push({
                        index: i,
                        src: src.substring(0, 120),
                        visible: f.offsetParent !== null,
                        rect: {w: Math.round(rect.width), h: Math.round(rect.height),
                               x: Math.round(rect.x), y: Math.round(rect.y)},
                        overflow: window.getComputedStyle(f.parentElement).overflow
                    });
                }
            });
            // 页面关键文本
            var body = document.body ? document.body.innerText.substring(0, 500) : '';
            result.body_text = body;
            result.title = document.title;
            result.url = window.location.href;
            return JSON.stringify(result);
        })()
        """)
        print(f"{log_prefix}📊 Turnstile 诊断: {diag}")
        return diag
    except Exception as e:
        print(f"{log_prefix}⚠️ 诊断失败: {e}")
        return ""


def click_turnstile_js(sb) -> bool:
    """尝试用 JS 直接触发 Turnstile 验证"""
    try:
        result = sb.execute_script("""
        (function(){
            var frames = document.querySelectorAll('iframe');
            for (var i = 0; i < frames.length; i++) {
                var src = frames[i].src || '';
                if (src.includes('challenges.cloudflare.com') || src.includes('turnstile')) {
                    // 方法1: 直接点击 iframe
                    try { frames[i].click(); } catch(e) {}
                    // 方法2: dispatchEvent
                    try {
                        var evt = new MouseEvent('click', {bubbles: true, cancelable: true, view: window});
                        frames[i].dispatchEvent(evt);
                    } catch(e) {}
                    return 'clicked_iframe_' + i;
                }
            }
            // 方法3: 在 iframe 内找 checkbox
            var ts_input = document.querySelector('input[name="cf-turnstile-response"]');
            if (ts_input) {
                // 触发 Turnstile 的隐式验证
                ts_input.dispatchEvent(new Event('focus', {bubbles: true}));
                ts_input.dispatchEvent(new Event('click', {bubbles: true}));
                return 'triggered_input';
            }
            return 'no_turnstile_found';
        })()
        """)
        print(f"   JS 点击结果: {result}")
        return True
    except Exception as e:
        print(f"   JS 点击异常: {e}")
        return False


def handle_turnstile(sb, max_attempts=6) -> bool:
    """
    增强版 Turnstile 处理（katabump 风格 + 多策略 fallback）：
    1. 诊断页面状态
    2. 展开 iframe
    3. 多策略点击：uc_gui_click_captcha → JS 点击 → iframe 切换点击
    """
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    # 诊断：查看 Turnstile 状态
    diagnose_turnstile(sb, "   ")

    # 检查是否已静默通过
    if check_token(sb):
        print("✅ 已静默通过")
        return True

    # 尝试展开 Turnstile（防止被父容器 overflow:hidden 裁剪）
    for _ in range(3):
        try:
            sb.execute_script(_TURNSTILE_EXPAND_JS)
        except Exception:
            pass
        time.sleep(0.5)

    # ===== 多策略尝试 =====
    for attempt in range(max_attempts):
        if check_token(sb):
            print(f"✅ Turnstile 通过（第 {attempt} 次尝试前已通过）")
            return True

        print(f"🔄 第 {attempt + 1}/{max_attempts} 轮尝试...")

        # 策略1: seleniumbase uc_gui_click_captcha
        print(f"   🖱️ 策略1: uc_gui_click_captcha...")
        try:
            sb.uc_gui_click_captcha()
            for _ in range(8):
                time.sleep(0.5)
                if check_token(sb):
                    print(f"✅ Turnstile 通过（策略1，第 {attempt + 1} 次）")
                    return True
        except Exception as e:
            print(f"   ⚠️ 策略1 异常: {e}")

        # 策略2: JS 直接触发 iframe 点击
        print(f"   🖱️ 策略2: JS 点击 iframe...")
        click_turnstile_js(sb)
        for _ in range(8):
            time.sleep(0.5)
            if check_token(sb):
                print(f"✅ Turnstile 通过（策略2，第 {attempt + 1} 次）")
                return True

        # 策略3: 切换 iframe 再点击 checkbox
        print(f"   🖱️ 策略3: iframe 切换点击...")
        try:
            frames = sb.find_elements("iframe")
            for frame in frames:
                try:
                    src = frame.get_attribute("src") or ""
                    if "challenges.cloudflare.com" in src or "turnstile" in src:
                        sb.driver.switch_to.frame(frame)
                        try:
                            cb = sb.find_element("input[type='checkbox']")
                            cb.click()
                            print(f"   ✅ iframe 内 checkbox 点击成功")
                        except:
                            pass
                        sb.driver.switch_to.default_content()
                        break
                except:
                    try:
                        sb.driver.switch_to.default_content()
                    except:
                        pass
        except Exception as e:
            print(f"   ⚠️ 策略3 异常: {e}")

        for _ in range(8):
            time.sleep(0.5)
            if check_token(sb):
                print(f"✅ Turnstile 通过（策略3，第 {attempt + 1} 次）")
                return True

        print(f"⚠️ 第 {attempt + 1} 轮所有策略均未通过，重试...")

    print(f"  ❌ Turnstile {max_attempts} 轮均失败")
    # 保存诊断截图
    try:
        sb.save_screenshot("turnstile_failed.png")
        print("  📸 已保存诊断截图: turnstile_failed.png")
    except:
        pass
    return False


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

    # ============================================================
    # 过盾方法（完全按 katabump 脚本的方式：等待页面加载，不盲目点验证）
    # ============================================================
    def cloudflare_all_page(self, sb):
        """
        仿 katabump 脚本的过盾方式：
        1. uc_open_with_reconnect 已经处理了 JS Challenge
        2. 等待页面上 Discord 登录按钮出现（最多等 30 秒）
        3. 如果页面正常加载，说明 CF 已过
        4. 如果页面还是 Just a moment，尝试 reconnect 再等
        """
        self.log("⏳ 等待 Cloudflare 验证通过（仿 katabump 方式）...")

        # 先检查当前页面标题
        try:
            title = sb.get_title() or ""
            url = sb.get_current_url() or ""
            self.log(f"📄 当前标题: {title}, URL: {url}")
        except:
            pass

        # 等待 Discord 登录按钮或页面非 CF 内容出现（最多 30 秒）
        cf_indicators = ["verify you are human", "确认您是真人", "troubleshoot",
                         "just a moment", "checking your browser", "performing security"]
        page_loaded = False

        for i in range(30):
            try:
                page_lower = sb.get_page_source().lower()
                # 检查是否还有 CF 关键词
                still_cf = any(x in page_lower for x in cf_indicators)
                if not still_cf:
                    self.log(f"✅ Cloudflare 验证已通过（{i+1}s）")
                    page_loaded = True
                    break

                # 同时检查页面上是否有 Discord 按钮（说明页面已加载）
                try:
                    if sb.is_element_visible('a[href="/auth/discord"]'):
                        self.log(f"✅ 页面已加载，Discord 按钮可见（{i+1}s）")
                        page_loaded = True
                        break
                except:
                    pass
            except Exception:
                pass
            time.sleep(1)

        if page_loaded:
            self.log("✅ CF 挑战已通过，页面正常加载")
            return True

        # 如果等待超时，尝试 reconnect
        self.log("⚠️ 30秒等待超时，尝试 reconnect...")
        try:
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=45)
            time.sleep(10)

            # 再等一次
            for i in range(30):
                try:
                    page_lower = sb.get_page_source().lower()
                    if not any(x in page_lower for x in cf_indicators):
                        self.log(f"✅ reconnect 后 CF 通过（{i+1}s）")
                        return True
                except:
                    pass
                time.sleep(1)
        except Exception as e:
            self.log(f"⚠️ reconnect 异常: {e}")

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
            headless=False,
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
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=8)
                time.sleep(6)
                
                # 仿 katabump 脚本：等待 CF 通过，检查页面是否加载
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

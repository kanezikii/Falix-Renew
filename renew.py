#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Falix Renew Pro v2

当前 Falix Timer 自动检查/续期程序。
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    from cloakbrowser import launch_context_async as cloakbrowser_launch_async
    HAS_CLOAKBROWSER = True
except ImportError:
    HAS_CLOAKBROWSER = False
    from playwright.async_api import async_playwright

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

# ============================================================
# Config
# ============================================================

BASE_URL = "https://client.falixnodes.net"
TIMER_URL = BASE_URL + "/timer?id={}"
DEBUG_DIR = Path("debug_output")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

SERVER_IDS = [
    x.strip()
    for x in os.getenv("FALIX_SERVER_IDS", "").split(",")
    if x.strip()
]

STORAGE_STATE = os.getenv("FALIX_STORAGE_STATE", "falix_state.json").strip()
RENEW_BELOW_MINUTES = int(os.getenv("RENEW_BELOW_MINUTES", "20"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# 优先使用专用代理变量 FALIX_PROXY，避免污染系统的 HTTP_PROXY
FALIX_PROXY = os.getenv("FALIX_PROXY", "").strip()
PROXY_URI = os.getenv("PROXY_URI", "").strip()
USE_PROXY = bool(FALIX_PROXY or PROXY_URI or os.getenv("ALL_PROXY"))
PROXY_SOCKS5 = FALIX_PROXY if FALIX_PROXY else "socks5://127.0.0.1:1080"

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()


# ============================================================
# Cookie / Storage State Sanitizer
# ============================================================

def sanitize_storage_state(file_path: str):
    """确保 storage_state 严格符合 Playwright 规范，移除冗余字段并标准化 sameSite"""
    path = Path(file_path)
    if not path.exists():
        return

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    if isinstance(raw_data, list):
        data = {"cookies": raw_data, "origins": []}
    elif isinstance(raw_data, dict):
        data = raw_data
    else:
        return

    cleaned_cookies = []
    for c in data.get("cookies", []):
        if not isinstance(c, dict):
            continue

        # 规范化 sameSite: Strict / Lax / None
        ss = str(c.get("sameSite", "")).lower()
        if ss in ("strict",):
            same_site = "Strict"
        elif ss in ("none", "no_restriction"):
            same_site = "None"
        else:
            same_site = "Lax"

        # 规范化 expires
        expires = c.get("expires", c.get("expirationDate", -1))
        try:
            expires = float(expires) if expires is not None else -1.0
        except (ValueError, TypeError):
            expires = -1.0

        cleaned_cookie = {
            "name": str(c.get("name", "")),
            "value": str(c.get("value", "")),
            "domain": str(c.get("domain", "")),
            "path": str(c.get("path", "/")),
            "expires": expires,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
            "sameSite": same_site,
        }
        cleaned_cookies.append(cleaned_cookie)

    data["cookies"] = cleaned_cookies
    if "origins" not in data or not isinstance(data["origins"], list):
        data["origins"] = []

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ============================================================
# Logging & Debug
# ============================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message):
    print(f"[{now()}] {message}", flush=True)


def safe_name(server_id):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", server_id)


async def save_debug(page, server_id, name):
    prefix = DEBUG_DIR / f"{safe_name(server_id)}_{name}"
    try:
        await page.screenshot(path=str(prefix.with_suffix(".png")), full_page=True)
        log(f"📸 Screenshot: {prefix}.png")
    except Exception as e:
        log(f"⚠️ Screenshot 失败: {e}")

    try:
        html = await page.content()
        prefix.with_suffix(".html").write_text(html, encoding="utf-8")
        log(f"📄 HTML: {prefix}.html")
    except Exception as e:
        log(f"⚠️ HTML 保存失败: {e}")


def send_tg(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
    except Exception as e:
        log(f"⚠️ Telegram 发送失败: {e}")


# ============================================================
# Time Parser & Text Extraction
# ============================================================

def parse_time(text):
    if not text:
        return None
    text = text.strip().lower()

    match = re.search(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", text)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))

    match = re.search(r"\b(\d{1,3}):(\d{2})\b", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))

    hours = re.search(r"(\d+)\s*(?:h|小时|时)\b", text)
    minutes = re.search(r"(\d+)\s*(?:m|分钟|分)\b", text)
    seconds = re.search(r"(\d+)\s*(?:s|秒)\b", text)

    if hours or minutes or seconds:
        h = int(hours.group(1)) if hours else 0
        m = int(minutes.group(1)) if minutes else 0
        s = int(seconds.group(1)) if seconds else 0
        return h * 3600 + m * 60 + s

    return None


async def get_body_text(page):
    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


async def get_remaining_seconds(page):
    text = await get_body_text(page)
    if not text:
        return None

    clean = re.sub(r"\s+", " ", text)
    log("📄 页面文本: " + clean[:600])

    values = re.findall(r"\b\d{1,3}:\d{2}:\d{2}\b", text)
    for value in values:
        seconds = parse_time(value)
        if seconds is not None and seconds <= 7 * 24 * 3600:
            return seconds

    values = re.findall(r"\b\d{1,3}:\d{2}\b", text)
    for value in values:
        seconds = parse_time(value)
        if seconds is not None and seconds <= 24 * 3600:
            return seconds

    seconds = parse_time(text)
    if seconds is not None and seconds <= 7 * 24 * 3600:
        return seconds

    return None


def format_seconds(seconds):
    if seconds is None:
        return "UNKNOWN"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ============================================================
# CAPTCHA Detection & Solver
# ============================================================

async def detect_captcha(page):
    try:
        has_response = await page.evaluate("""
        (() => {
            const inputs = document.querySelectorAll(
                'input[name="cf-turnstile-response"], input[id*="cf-chl-widget"][name="cf-turnstile-response"]'
            );
            for (const inp of inputs) {
                if (inp.value && inp.value.length > 10) {
                    return true;
                }
            }
            return false;
        })();
        """)
        if has_response:
            return False
    except Exception:
        pass

    patterns = ["captcha", "turnstile", "verify you are human", "verification", "cloudflare", "challenge"]
    text = (await get_body_text(page)).lower()

    managed_challenge_text = any(
        phrase in text
        for phrase in [
            "just a moment",
            "performing security verification",
            "this website uses a security service",
        ]
    )

    for pattern in patterns:
        if pattern in text:
            if managed_challenge_text:
                return True
            break

    selectors = [
        'iframe[src*="turnstile"]',
        'iframe[src*="captcha"]',
        '[class*="captcha"]',
        '[id*="captcha"]',
    ]

    for selector in selectors:
        try:
            if await page.locator(selector).count():
                return True
        except Exception:
            pass

    return False


async def find_turnstile_iframe_box(page):
    try:
        for fr in page.frames:
            furl = (fr.url or "").lower()
            if "challenges.cloudflare.com" in furl or "turnstile" in furl or "captcha" in furl:
                try:
                    iframe_el = await fr.frame_element()
                    if iframe_el:
                        box = await iframe_el.bounding_box()
                        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                            return iframe_el, box
                except Exception:
                    pass
    except Exception:
        pass

    for sel in [
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="turnstile"]',
        'iframe[src*="captcha"]',
        'iframe[src*="cloudflare"]',
        'div.cf-turnstile iframe',
        'div[data-sitekey] iframe',
    ]:
        try:
            iframe_el = await page.query_selector(sel)
            if iframe_el:
                box = await iframe_el.bounding_box()
                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                    return iframe_el, box
        except Exception:
            pass

    return None, None


async def solve_captcha(page, max_wait=120, click_after=8):
    import random as _rand
    log(f"🛡️ 等待 CAPTCHA/Turnstile 自动通过 (最长 {max_wait}s, {click_after}s 后坐标点击)...")
    end_time = time.time() + max_wait
    iframe_first_seen = None
    click_count = 0

    while time.time() < end_time:
        try:
            has_captcha = await detect_captcha(page)
            if not has_captcha:
                if iframe_first_seen:
                    elapsed = time.time() - iframe_first_seen
                    log(f"  ✅ CAPTCHA/Challenge 已通过 (耗时 {elapsed:.1f}s)")
                else:
                    log("  ✅ 未出现 CAPTCHA, 无需验证")
                return True

            if iframe_first_seen is None:
                iframe_first_seen = time.time()
                log("  📍 检测到 CAPTCHA/Turnstile")

            if time.time() - iframe_first_seen >= click_after:
                _, box = await find_turnstile_iframe_box(page)
                if box:
                    click_offsets = [
                        (max(20, box["width"] * 0.08), box["height"] * 0.30),
                        (max(30, box["width"] * 0.10), box["height"] * 0.40),
                    ]
                    off = click_offsets[click_count % len(click_offsets)]
                    target_x = box["x"] + off[0]
                    target_y = box["y"] + off[1]
                    try:
                        await page.mouse.move(box["x"] + _rand.uniform(10, 50), box["y"] + _rand.uniform(10, 30))
                        await asyncio.sleep(0.3)
                        await page.mouse.click(target_x, target_y)
                        click_count += 1
                        log(f"  🖱️ 第 {click_count} 次坐标点击 Turnstile 复选框 ({target_x:.0f}, {target_y:.0f})")
                        await asyncio.sleep(6)
                        continue
                    except Exception as e:
                        log(f"  ⚠️ 坐标点击异常: {e}")

            await asyncio.sleep(3)
        except Exception as e:
            log(f"  ⚠️ CAPTCHA 循环异常: {e}")
            await asyncio.sleep(2)

    log(f"⏰ CAPTCHA 验证超时 ({max_wait}s)")
    return False


# ============================================================
# Login & Add Time Actions
# ============================================================

async def check_login(page):
    url = page.url.lower()
    if "/login" in url or "/auth" in url:
        return False
    text = (await get_body_text(page)).lower()
    for word in ["log in", "login", "sign in"]:
        if word in text and "timer" not in text and "server" not in text:
            return False
    return True


async def find_add_time_button(page):
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*Add\s+Time\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*添加\s*时间\s*$")),
        page.get_by_text(re.compile(r"^\s*Add\s+Time\s*$", re.I)),
        page.get_by_text(re.compile(r"^\s*添加\s*时间\s*$")),
        page.locator("button").filter(has_text=re.compile(r"Add\s*Time", re.I)),
        page.locator("button").filter(has_text=re.compile(r"添加\s*时间")),
        page.locator(":has-text('添加时间')").filter(has_text=re.compile(r"添加\s*时间")),
    ]
    for locator in candidates:
        try:
            count = await locator.count()
            for index in range(count):
                item = locator.nth(index)
                if await item.is_visible():
                    return item
        except Exception:
            continue
    return None


async def click_add_time(page, server_id):
    button = await find_add_time_button(page)
    if button is None:
        log("❌ 找不到 Add Time 按钮")
        await save_debug(page, server_id, "add_time_missing")
        return False

    try:
        await button.scroll_into_view_if_needed()
        await page.wait_for_timeout(1000)
        log("🖱️ 点击 Add Time")
        await button.click(timeout=10000)
        return True
    except Exception as e:
        log(f"❌ Add Time 点击失败: {e}")
        await save_debug(page, server_id, "click_error")
        return False


async def wait_timer_change(page, server_id, before, timeout=40):
    end_time = time.time() + timeout
    while time.time() < end_time:
        await page.wait_for_timeout(2000)
        current = await get_remaining_seconds(page)
        if current is None:
            continue
        log(f"⏱️ Timer: {format_seconds(before)} -> {format_seconds(current)}")
        if before is None:
            if current > 0:
                return True
        elif current > before + 30:
            return True
    return False


# ============================================================
# Core Server Logic
# ============================================================

async def process_server(page, server_id):
    log("")
    log("=" * 70)
    log(f"🚀 开始处理服务器: {server_id}")
    log("=" * 70)

    url = TIMER_URL.format(server_id)
    log(f"🌐 {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeoutError:
        log("⚠️ 页面加载超时，继续检查")
    except Exception as e:
        log(f"❌ 打开页面失败: {e}")
        await save_debug(page, server_id, "goto_error")
        return False

    await page.wait_for_timeout(3000)

    if not await check_login(page):
        log("❌ Falix 登录状态失效")
        await save_debug(page, server_id, "login_required")
        send_tg(f"❌ Falix 登录状态失效\nServer ID: {server_id}")
        return False

    if await detect_captcha(page):
        log("🛡️ 检测到 CAPTCHA / Turnstile, 尝试自动通过...")
        await save_debug(page, server_id, "captcha_before")
        if not await solve_captcha(page, max_wait=120, click_after=8):
            log("❌ CAPTCHA 自动通过失败")
            await save_debug(page, server_id, "captcha_failed")
            send_tg(f"⚠️ Falix CAPTCHA 自动通过失败\nServer ID: {server_id}")
            return False
        await save_debug(page, server_id, "captcha_passed")

    before = await get_remaining_seconds(page)
    log(f"⏱️ 当前 Timer: {format_seconds(before)}")

    if before is not None:
        threshold = RENEW_BELOW_MINUTES * 60
        if before > threshold:
            log("✅ Timer 尚未低于阈值，无需续期")
            send_tg(f"⏭️ Falix Timer 充足, 无需续期\nServer ID: {server_id}\nTimer: {format_seconds(before)}")
            return True

    if not await click_add_time(page, server_id):
        send_tg(f"❌ Falix Add Time 按钮异常\nServer ID: {server_id}")
        return False

    await page.wait_for_timeout(2000)

    if await detect_captcha(page):
        log("🛡️ 点击 Add Time 后出现 CAPTCHA, 尝试通过...")
        if not await solve_captcha(page, max_wait=120, click_after=8):
            send_tg(f"⚠️ Falix 点击 Add Time 后 CAPTCHA 失败\nServer ID: {server_id}")
            return False

    success = await wait_timer_change(page, server_id, before)
    after = await get_remaining_seconds(page)

    if success:
        log(f"🎉 续期成功，新 Timer: {format_seconds(after)}")
        send_tg(f"✅ Falix 续期成功\nServer ID: {server_id}\nTimer: {format_seconds(after)}")
        await save_debug(page, server_id, "success")
        return True

    log("❌ 点击后 Timer 没有增加")
    await save_debug(page, server_id, "renew_failed")
    send_tg(f"❌ Falix 续期失败\nServer ID: {server_id}\nBefore: {format_seconds(before)}\nAfter: {format_seconds(after)}")
    return False


# ============================================================
# Main Entrypoint
# ============================================================

async def main():
    if not SERVER_IDS:
        log("❌ 没有设置 FALIX_SERVER_IDS")
        sys.exit(1)

    # 运行前对 storage_state 做深度清洗校验
    sanitize_storage_state(STORAGE_STATE)

    if not Path(STORAGE_STATE).exists():
        log(f"❌ storage_state 不存在: {STORAGE_STATE}")
        os._exit(1)

    log(f"🔍 Browser engine: {'cloakbrowser (stealth)' if HAS_CLOAKBROWSER else 'playwright (raw)'}")
    log(f"🔍 Proxy: {PROXY_SOCKS5 if USE_PROXY else 'disabled (direct)'}")

    proxy_arg = PROXY_SOCKS5 if USE_PROXY else None

    if HAS_CLOAKBROWSER:
        context = await cloakbrowser_launch_async(
            headless=HEADLESS,
            proxy=proxy_arg,
            geoip=True,
            humanize=True,
            storage_state=STORAGE_STATE,
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        page.set_default_timeout(15000)

        results = []
        for server_id in SERVER_IDS:
            try:
                res = await process_server(page, server_id)
            except Exception as e:
                log(f"❌ 未处理异常: {e}")
                await save_debug(page, server_id, "exception")
                send_tg(f"💥 Falix 脚本异常\nServer ID: {server_id}\nError: {e}")
                res = False
            results.append((server_id, res))

        await context.close()
    else:
        async with async_playwright() as playwright:
            p_arg = {"server": proxy_arg} if proxy_arg else None
            browser = await playwright.chromium.launch(
                headless=HEADLESS,
                proxy=p_arg,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = await browser.new_context(
                storage_state=STORAGE_STATE,
                viewport={"width": 1440, "height": 1000},
                locale="en-US",
                timezone_id="Asia/Tokyo",
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            results = []
            for server_id in SERVER_IDS:
                try:
                    res = await process_server(page, server_id)
                except Exception as e:
                    log(f"❌ 未处理异常: {e}")
                    await save_debug(page, server_id, "exception")
                    send_tg(f"💥 Falix 脚本异常\nServer ID: {server_id}\nError: {e}")
                    res = False
                results.append((server_id, res))

            await context.close()
            await browser.close()

    success_count = sum(1 for _, r in results if r)
    log(f"成功: {success_count}/{len(results)}")

    summary_lines = ["🎮 Falix 自动续期", f"✅ 成功: {success_count}/{len(results)}"]
    for server_id, result in results:
        st = "✅ OK" if result else "❌ FAILED"
        summary_lines.append(f"{st} {server_id}")
    send_tg("\n".join(summary_lines))

    if success_count != len(results):
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())

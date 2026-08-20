#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Falix Renew Pro v1.0
- Playwright
- FalixNodes Timer/Extend 自动检测
- CAPTCHA / Turnstile 保留人工完成
- 自动检测续期结果
- Telegram 通知
- 支持多个服务器
"""

import os
import re
import sys
import time
import json
import asyncio
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# 配置
# ============================================================

FALIX_EMAIL = os.getenv("FALIX_EMAIL", "")
FALIX_PASSWORD = os.getenv("FALIX_PASSWORD", "")

# 推荐直接使用 Cookie Storage State
STORAGE_STATE = os.getenv("FALIX_STORAGE_STATE", "falix_state.json")

# 服务器配置：
# FALIX_SERVERS="服务器1:123456;服务器2:234567"
SERVER_CONFIG = os.getenv("FALIX_SERVERS", "")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# 剩余时间低于这个值才尝试续期
RENEW_BELOW_MINUTES = int(os.getenv("RENEW_BELOW_MINUTES", "30"))

# CAPTCHA 最大等待时间
CAPTCHA_WAIT_SECONDS = int(os.getenv("CAPTCHA_WAIT_SECONDS", "300"))

# 页面超时
PAGE_TIMEOUT = 30000


# ============================================================
# 日志
# ============================================================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}", flush=True)


# ============================================================
# Telegram
# ============================================================

def tg_send(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

        requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

    except Exception as e:
        log(f"Telegram 通知失败: {e}")


# ============================================================
# Timer 解析
# ============================================================

def parse_timer(text):
    """
    支持：

    01:23:45
    23:45
    1h 23m
    1h 23m 45s
    """

    if not text:
        return None

    text = text.strip().lower()

    # HH:MM:SS
    m = re.search(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", text)

    if m:
        h = int(m.group(1))
        mi = int(m.group(2))
        s = int(m.group(3))

        return h * 3600 + mi * 60 + s

    # MM:SS
    m = re.search(r"\b(\d{1,3}):(\d{2})\b", text)

    if m:
        mi = int(m.group(1))
        s = int(m.group(2))

        return mi * 60 + s

    # 1h 20m 30s
    total = 0

    mh = re.search(r"(\d+)\s*h", text)
    mm = re.search(r"(\d+)\s*m", text)
    ms = re.search(r"(\d+)\s*s", text)

    if mh:
        total += int(mh.group(1)) * 3600

    if mm:
        total += int(mm.group(1)) * 60

    if ms:
        total += int(ms.group(1))

    return total if total > 0 else None


def format_seconds(seconds):
    if seconds is None:
        return "未知"

    seconds = max(0, int(seconds))

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h:02d}:{m:02d}:{s:02d}"


# ============================================================
# 服务器配置
# ============================================================

def load_servers():
    """
    FALIX_SERVERS:

    服务器A:123456
    服务器B:234567
    """

    servers = []

    if not SERVER_CONFIG:
        return servers

    for item in SERVER_CONFIG.split(";"):

        item = item.strip()

        if not item:
            continue

        if ":" in item:
            name, server_id = item.rsplit(":", 1)
        else:
            name = item
            server_id = ""

        servers.append({
            "name": name.strip(),
            "id": server_id.strip(),
        })

    return servers


# ============================================================
# 页面辅助
# ============================================================

async def save_debug(page, name):

    os.makedirs("debug_output", exist_ok=True)

    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

    try:
        await page.screenshot(
            path=f"debug_output/{safe}.png",
            full_page=True
        )
    except Exception:
        pass

    try:
        html = await page.content()

        with open(
            f"debug_output/{safe}.html",
            "w",
            encoding="utf-8"
        ) as f:
            f.write(html)

    except Exception:
        pass


async def page_text(page):

    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


# ============================================================
# 登录
# ============================================================

async def ensure_login(page):

    log("检查 Falix 登录状态...")

    await page.goto(
        "https://client.falixnodes.net/",
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT
    )

    await page.wait_for_timeout(3000)

    url = page.url.lower()

    text = await page_text(page)

    if "login" not in url and "sign in" not in text.lower():
        log("✅ 已登录 Falix")
        return True

    if not FALIX_EMAIL or not FALIX_PASSWORD:
        log("⚠️ 当前没有有效登录状态")
        log("请先手动登录 Falix，然后保存 Storage State")
        return False

    log("正在登录 Falix...")

    try:

        await page.goto(
            "https://client.falixnodes.net/login",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        await page.wait_for_timeout(2000)

        # 尝试常见输入框
        email = page.locator(
            'input[type="email"], input[name="email"], input[placeholder*="email" i]'
        ).first

        password = page.locator(
            'input[type="password"], input[name="password"]'
        ).first

        await email.fill(FALIX_EMAIL)
        await password.fill(FALIX_PASSWORD)

        await page.locator(
            'button:has-text("Login"), '
            'button:has-text("Sign in"), '
            'button[type="submit"]'
        ).first.click()

        log("登录请求已提交")

        # CAPTCHA 不自动绕过
        await page.wait_for_timeout(3000)

        if "login" in page.url.lower():

            log("⚠️ 登录可能需要 CAPTCHA")

            print()
            print("=" * 60)
            print("请在浏览器中手动完成 Falix CAPTCHA / 登录验证")
            print("=" * 60)
            print()

            deadline = time.time() + CAPTCHA_WAIT_SECONDS

            while time.time() < deadline:

                if "login" not in page.url.lower():

                    log("✅ 登录完成")
                    return True

                await page.wait_for_timeout(2000)

            log("❌ 登录等待超时")
            return False

        log("✅ 登录成功")

        return True

    except Exception as e:

        log(f"❌ 登录失败: {e}")

        await save_debug(page, "login_error")

        return False


# ============================================================
# 获取 Timer
# ============================================================

async def get_timer(page):

    text = await page_text(page)

    # 优先搜索明确的 Timer
    patterns = [
        r"SERVER\s*TIMER.{0,100}?(\d{1,3}:\d{2}:\d{2})",
        r"TIMER.{0,100}?(\d{1,3}:\d{2}:\d{2})",
        r"(\d{1,3}:\d{2}:\d{2})",
    ]

    for pattern in patterns:

        m = re.search(pattern, text, re.I | re.S)

        if m:
            value = parse_timer(m.group(1))

            if value is not None:
                return value

    return None


# ============================================================
# 查找 Extend
# ============================================================

async def find_extend(page):

    selectors = [
        'button:has-text("Extend")',
        'a:has-text("Extend")',
        'button:has-text("Add Time")',
        'a:has-text("Add Time")',
        'button:has-text("Add time")',
        'a:has-text("Add time")',
        'text=Extend',
        'text=Add Time',
    ]

    for selector in selectors:

        try:

            loc = page.locator(selector).first

            if await loc.count() and await loc.is_visible():

                log(f"🔎 找到续期按钮: {selector}")

                return loc

        except Exception:
            continue

    return None


# ============================================================
# CAPTCHA 检测
# ============================================================

async def captcha_present(page):

    text = (await page_text(page)).lower()

    keywords = [
        "captcha",
        "turnstile",
        "verify you are human",
        "checking your browser",
        "cloudflare",
    ]

    if any(x in text for x in keywords):
        return True

    try:

        if await page.locator(
            'iframe[src*="challenges.cloudflare.com"]'
        ).count():
            return True

    except Exception:
        pass

    return False


async def wait_captcha(page):

    if not await captcha_present(page):
        return True

    log("🛡️ 检测到 CAPTCHA / Turnstile")
    log("👉 请在浏览器中手动完成验证")
    log("⏳ 等待验证完成...")

    deadline = time.time() + CAPTCHA_WAIT_SECONDS

    while time.time() < deadline:

        if not await captcha_present(page):

            log("✅ CAPTCHA 验证完成")

            await page.wait_for_timeout(2000)

            return True

        await page.wait_for_timeout(2000)

    log("❌ CAPTCHA 等待超时")

    return False


# ============================================================
# 执行续期
# ============================================================

async def renew_server(page, server):

    name = server["name"]
    server_id = server["id"]

    log("")
    log("=" * 60)
    log(f"开始处理服务器: {name}")
    log("=" * 60)

    if server_id:

        url = f"https://client.falixnodes.net/timer?id={server_id}"

    else:

        url = "https://client.falixnodes.net/"

    log(f"打开: {url}")

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        await page.wait_for_timeout(3000)

    except Exception as e:

        log(f"❌ 页面打开失败: {e}")

        await save_debug(page, f"{name}_open_error")

        return False

    # 检查登录
    if "login" in page.url.lower():

        log("⚠️ 登录状态失效")

        if not await ensure_login(page):
            return False

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        await page.wait_for_timeout(3000)

    before = await get_timer(page)

    if before is not None:

        log(
            f"⏱️ 当前 Timer: "
            f"{format_seconds(before)}"
        )

        if before > RENEW_BELOW_MINUTES * 60:

            log(
                f"✅ 剩余时间超过 "
                f"{RENEW_BELOW_MINUTES} 分钟，暂不续期"
            )

            return True

    else:

        log("⚠️ 暂时无法读取 Timer")

    # 找 Extend
    extend = await find_extend(page)

    if not extend:

        log("❌ 找不到 Extend / Add Time")

        await save_debug(page, f"{name}_extend_not_found")

        return False

    try:

        await extend.scroll_into_view_if_needed()

        await page.wait_for_timeout(500)

        await extend.click()

        log("👉 已点击 Extend / Add Time")

    except Exception as e:

        log(f"❌ 点击续期按钮失败: {e}")

        await save_debug(page, f"{name}_click_error")

        return False

    await page.wait_for_timeout(2000)

    # CAPTCHA
    if not await wait_captcha(page):

        return False

    # 再次尝试寻找 Add Time
    add_time = await find_extend(page)

    if add_time:

        try:

            if await add_time.is_enabled():

                await add_time.click()

                log("👉 已点击 Add Time")

        except Exception as e:

            log(f"⚠️ Add Time 点击失败: {e}")

    # 等待结果
    log("⏳ 等待续期结果...")

    deadline = time.time() + 30

    while time.time() < deadline:

        await page.wait_for_timeout(2000)

        after = await get_timer(page)

        if after is not None:

            log(
                f"⏱️ 当前 Timer: "
                f"{format_seconds(after)}"
            )

            if before is not None:

                # 增加超过 2 分钟视为成功
                if after > before + 120:

                    log("🎉 续期成功！")

                    tg_send(
                        f"✅ Falix 续期成功\n\n"
                        f"服务器：{name}\n"
                        f"续期前：{format_seconds(before)}\n"
                        f"续期后：{format_seconds(after)}"
                    )

                    return True

        text = await page_text(page)

        success_words = [
            "successfully",
            "time added",
            "added time",
            "extension successful",
        ]

        if any(x in text.lower() for x in success_words):

            log("🎉 页面显示续期成功")

            tg_send(
                f"✅ Falix 续期成功\n\n"
                f"服务器：{name}"
            )

            return True

    log("❌ 无法确认续期成功")

    await save_debug(page, f"{name}_renew_unknown")

    tg_send(
        f"⚠️ Falix 续期结果未知\n\n"
        f"服务器：{name}\n"
        f"请检查 debug_output"
    )

    return False


# ============================================================
# 保存登录状态
# ============================================================

async def save_state(browser):

    context = await browser.new_context(
        viewport={
            "width": 1440,
            "height": 900
        }
    )

    page = await context.new_page()

    log("打开 Falix 登录页面")

    await page.goto(
        "https://client.falixnodes.net/login",
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT
    )

    print()
    print("=" * 60)
    print("请手动登录 Falix")
    print("如果出现 CAPTCHA / Turnstile，请正常完成")
    print("登录完成后回到终端按 Enter")
    print("=" * 60)
    print()

    await asyncio.to_thread(input)

    await context.storage_state(path=STORAGE_STATE)

    log(f"✅ 登录状态已保存: {STORAGE_STATE}")

    await context.close()


# ============================================================
# 主程序
# ============================================================

async def main():

    log("🚀 Falix Renew Pro v1.0 启动")

    servers = load_servers()

    if not servers:

        log("⚠️ 没有配置 FALIX_SERVERS")

        log(
            '示例：'
            'FALIX_SERVERS="服务器1:123456;服务器2:234567"'
        )

        return

    log(f"📋 共发现 {len(servers)} 台服务器")

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=HEADLESS
        )

        # ====================================================
        # 没有 Storage State
        # ====================================================

        if not os.path.exists(STORAGE_STATE):

            log("⚠️ 未发现 Falix 登录状态")

            await save_state(browser)

            await browser.close()

            return

        # ====================================================
        # 使用保存的登录状态
        # ====================================================

        context = await browser.new_context(
            storage_state=STORAGE_STATE,
            viewport={
                "width": 1440,
                "height": 900
            }
        )

        page = await context.new_page()

        page.set_default_timeout(PAGE_TIMEOUT)

        # 登录检查
        if not await ensure_login(page):

            log("❌ Falix 登录失败")

            await browser.close()

            return

        success = 0
        failed = 0

        for server in servers:

            try:

                result = await renew_server(
                    page,
                    server
                )

                if result:
                    success += 1
                else:
                    failed += 1

            except Exception as e:

                failed += 1

                log(
                    f"❌ {server['name']} "
                    f"处理异常: {e}"
                )

                await save_debug(
                    page,
                    f"{server['name']}_exception"
                )

            await page.wait_for_timeout(2000)

        # ====================================================
        # 最终通知
        # ====================================================

        summary = (
            "🚀 Falix Renew Pro 执行完成\n\n"
            f"服务器数量：{len(servers)}\n"
            f"成功：{success}\n"
            f"失败/未知：{failed}\n"
            f"时间：{now()}"
        )

        log(summary)

        tg_send(summary)

        await context.close()
        await browser.close()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:

        log("程序已停止")

    except Exception as e:

        log(f"程序异常退出: {e}")

        tg_send(
            f"❌ Falix Renew Pro 异常退出\n\n{e}"
        )

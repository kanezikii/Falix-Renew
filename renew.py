#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Falix Renew Pro v2
适配 Falix 当前 Timer 页面

功能：
- 使用 Playwright
- 支持多个服务器
- 直接访问当前 /timer?id=SERVER_ID
- 自动读取剩余时间
- 低于阈值才续期
- 自动寻找 Add Time
- 检测 CAPTCHA / Turnstile
- 不绕过 CAPTCHA
- 支持 Telegram 通知
- 截图 + HTML 调试
- GitHub Actions 兼容

环境变量：

FALIX_SERVER_IDS
    多个 Server ID，用逗号分隔

FALIX_STORAGE_STATE
    可选，Playwright storage_state JSON 文件路径

TG_BOT_TOKEN
TG_CHAT_ID

RENEW_BELOW_MINUTES
    默认 20

HEADLESS
    true / false
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://client.falixnodes.net"
TIMER_URL = BASE_URL + "/timer?id={}"

DEBUG_DIR = Path("debug_output")
DEBUG_DIR.mkdir(exist_ok=True)

RENEW_BELOW_MINUTES = int(
    os.getenv("RENEW_BELOW_MINUTES", "20")
)

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

SERVER_IDS = [
    x.strip()
    for x in os.getenv("FALIX_SERVER_IDS", "").split(",")
    if x.strip()
]

STORAGE_STATE = os.getenv("FALIX_STORAGE_STATE", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}", flush=True)


def safe_name(server_id):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", server_id)


async def save_debug(page, server_id, name):
    prefix = DEBUG_DIR / f"{safe_name(server_id)}_{name}"

    try:
        await page.screenshot(
            path=str(prefix.with_suffix(".png")),
            full_page=True
        )
    except Exception as e:
        log(f"截图失败: {e}")

    try:
        html = await page.content()
        prefix.with_suffix(".html").write_text(
            html,
            encoding="utf-8"
        )
    except Exception as e:
        log(f"HTML 保存失败: {e}")


def send_tg(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TG_BOT_TOKEN}/sendMessage"
        )

        requests.post(
            url,
            data={
                "chat_id": TG_CHAT_ID,
                "text": message,
            },
            timeout=15,
        )
    except Exception as e:
        log(f"Telegram 通知失败: {e}")


def parse_time(text):
    """
    支持：
    01:23:45
    23:45
    1h 23m 45s
    1h 23m
    """

    if not text:
        return None

    text = text.strip().lower()

    # HH:MM:SS
    m = re.search(
        r"(\d{1,3}):(\d{2}):(\d{2})",
        text
    )

    if m:
        h, mi, s = map(int, m.groups())
        return h * 3600 + mi * 60 + s

    # MM:SS
    m = re.search(
        r"(\d{1,3}):(\d{2})",
        text
    )

    if m:
        mi, s = map(int, m.groups())
        return mi * 60 + s

    # 1h 20m 30s
    h = re.search(r"(\d+)\s*h", text)
    mi = re.search(r"(\d+)\s*m", text)
    s = re.search(r"(\d+)\s*s", text)

    if h or mi or s:
        return (
            (int(h.group(1)) if h else 0) * 3600
            + (int(mi.group(1)) if mi else 0) * 60
            + (int(s.group(1)) if s else 0)
        )

    return None


async def get_timer_text(page):
    """
    当前 Falix Timer 页面会显示：

    SERVER STOPS IN
    --:--:--
    """

    selectors = [
        "text=SERVER STOPS IN",
        "text=Server Timer",
        "text=SERVER TIMER",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)

            if await loc.count() > 0:
                parent = loc.first.locator("..")

                try:
                    text = await parent.inner_text(
                        timeout=3000
                    )
                    if text:
                        return text
                except Exception:
                    pass
        except Exception:
            pass

    # 最后直接读取整个页面文本
    try:
        return await page.locator("body").inner_text()
    except Exception:
        return ""


async def get_remaining_seconds(page):
    text = await get_timer_text(page)

    log(
        "Timer 页面文本片段: "
        + re.sub(r"\s+", " ", text)[:500]
    )

    # 优先找 HH:MM:SS
    matches = re.findall(
        r"\b(\d{1,3}:\d{2}:\d{2})\b",
        text
    )

    for item in matches:
        seconds = parse_time(item)

        if seconds is not None:
            # 排除明显无效值
            if seconds <= 7 * 24 * 3600:
                return seconds

    # 再找 MM:SS
    matches = re.findall(
        r"\b(\d{1,3}:\d{2})\b",
        text
    )

    for item in matches:
        seconds = parse_time(item)

        if seconds is not None and seconds <= 24 * 3600:
            return seconds

    return None


async def detect_captcha(page):
    """
    只检测 CAPTCHA，不尝试绕过。
    """

    patterns = [
        "captcha",
        "turnstile",
        "verify you are human",
        "verification",
        "cloudflare",
        "challenge",
    ]

    try:
        body = (
            await page.locator("body").inner_text()
        ).lower()

        for p in patterns:
            if p in body:
                return True
    except Exception:
        pass

    try:
        if await page.locator(
            'iframe[src*="turnstile"]'
        ).count():
            return True

        if await page.locator(
            'iframe[src*="captcha"]'
        ).count():
            return True

        if await page.locator(
            '[class*="captcha"]'
        ).count():
            return True
    except Exception:
        pass

    return False


async def find_add_time_button(page):
    """
    当前页面按钮：

        Add Time

    同时兼容大小写/空白变化。
    """

    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*Add\s+Time\s*$",
                re.I
            )
        ),

        page.get_by_text(
            re.compile(
                r"^\s*Add\s+Time\s*$",
                re.I
            )
        ),

        page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"Add\s*Time",
                re.I
            )
        ),
    ]

    for loc in candidates:
        try:
            count = await loc.count()

            if count:
                for i in range(count):
                    item = loc.nth(i)

                    if await item.is_visible():
                        return item
        except Exception:
            continue

    return None


async def click_add_time(page, server_id):
    button = await find_add_time_button(page)

    if not button:
        log("❌ 没找到 Add Time 按钮")
        await save_debug(
            page,
            server_id,
            "no_add_time"
        )
        return False

    try:
        await button.scroll_into_view_if_needed()

        await page.wait_for_timeout(1000)

        log("🖱️ 点击 Add Time")

        await button.click(
            timeout=10000
        )

        await page.wait_for_timeout(3000)

        return True

    except Exception as e:
        log(f"❌ 点击 Add Time 失败: {e}")

        await save_debug(
            page,
            server_id,
            "add_time_error"
        )

        return False


async def wait_for_timer_change(
    page,
    server_id,
    before,
    timeout=30
):
    """
    点击后检测 Timer 是否增加。
    """

    end = time.time() + timeout

    while time.time() < end:
        await page.wait_for_timeout(2000)

        after = await get_remaining_seconds(page)

        if after is None:
            continue

        log(
            f"Timer: "
            f"{before if before is not None else '?'} "
            f"-> {after}"
        )

        if before is None:
            if after > 0:
                return True

        elif after > before + 30:
            return True

    return False


async def renew_server(page, server_id):
    url = TIMER_URL.format(server_id)

    log("")
    log("=" * 60)
    log(f"开始处理 Server ID: {server_id}")
    log("=" * 60)

    log(f"🌐 打开: {url}")

    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )
    except Exception as e:
        log(f"❌ 页面打开失败: {e}")

        await save_debug(
            page,
            server_id,
            "goto_error"
        )

        return False

    await page.wait_for_timeout(3000)

    # 登录状态检查
    current_url = page.url

    if "login" in current_url.lower():
        log("❌ 当前浏览器没有 Falix 登录状态")
        log("请先生成 Playwright storage_state")

        await save_debug(
            page,
            server_id,
            "login_required"
        )

        return False

    # 页面错误
    body = ""

    try:
        body = (
            await page.locator("body").inner_text()
        )
    except Exception:
        pass

    if "Server id is required" in body:
        log("❌ Falix 没有识别 Server ID")
        await save_debug(
            page,
            server_id,
            "server_id_error"
        )
        return False

    if "Timer has expired" in body:
        log("⚠️ Timer 已经过期")
        log("可以尝试通过 Timer 页面恢复")

    before = await get_remaining_seconds(page)

    if before is not None:
        log(
            f"⏱️ 当前剩余时间: "
            f"{before // 3600:02d}:"
            f"{(before % 3600) // 60:02d}:"
            f"{before % 60:02d}"
        )

        threshold = RENEW_BELOW_MINUTES * 60

        if before > threshold:
            log(
                f"✅ 剩余时间超过 "
                f"{RENEW_BELOW_MINUTES} 分钟"
                "，暂不续期"
            )
            return True

    # CAPTCHA
    if await detect_captcha(page):
        log("🛡️ 检测到 CAPTCHA / 人机验证")
        log("❗不会自动绕过 CAPTCHA")

        await save_debug(
            page,
            server_id,
            "captcha"
        )

        send_tg(
            f"⚠️ Falix 需要人工完成 CAPTCHA\n"
            f"Server ID: {server_id}"
        )

        return False

    button = await find_add_time_button(page)

    if not button:
        log("❌ 当前页面没有找到 Add Time")
        await save_debug(
            page,
            server_id,
            "button_missing"
        )

        send_tg(
            f"❌ Falix 续期按钮异常\n"
            f"Server ID: {server_id}"
        )

        return False

    log("✅ 找到 Add Time")

    # 再次检测 CAPTCHA
    if await detect_captcha(page):
        log("🛡️ 点击前检测到 CAPTCHA")
        return False

    if not await click_add_time(
        page,
        server_id
    ):
        return False

    # 点击后可能弹 CAPTCHA
    await page.wait_for_timeout(2000)

    if await detect_captcha(page):
        log(
            "🛡️ 点击后出现 CAPTCHA"
        )

        await save_debug(
            page,
            server_id,
            "captcha_after_click"
        )

        send_tg(
            f"⚠️ Falix CAPTCHA 出现\n"
            f"Server ID: {server_id}"
        )

        return False

    success = await wait_for_timer_change(
        page,
        server_id,
        before
    )

    if success:
        after = await get_remaining_seconds(page)

        log("🎉 Falix 续期成功")

        if after is not None:
            log(
                f"⏱️ 新 Timer: "
                f"{after // 3600:02d}:"
                f"{(after % 3600) // 60:02d}:"
                f"{after % 60:02d}"
            )

        send_tg(
            f"✅ Falix 续期成功\n"
            f"Server ID: {server_id}\n"
            f"Timer: "
            f"{after // 3600:02d}:"
            f"{(after % 3600) // 60:02d}:"
            f"{after % 60:02d}"
            if after is not None
            else
            f"✅ Falix 续期成功\n"
            f"Server ID: {server_id}"
        )

        await save_debug(
            page,
            server_id,
            "success"
        )

        return True

    log("❌ 点击后 Timer 没有明显增加")

    await save_debug(
        page,
        server_id,
        "renew_failed"
    )

    send_tg(
        f"❌ Falix 续期失败\n"
        f"Server ID: {server_id}"
    )

    return False


async def main():
    if not SERVER_IDS:
        log(
            "❌ 没有配置 FALIX_SERVER_IDS"
        )
        sys.exit(1)

    log("🚀 Falix Renew Pro v2")
    log(f"服务器数量: {len(SERVER_IDS)}")
    log(
        f"续期阈值: "
        f"{RENEW_BELOW_MINUTES} 分钟"
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context_args = {
            "viewport": {
                "width": 1440,
                "height": 1000,
            },
            "locale": "en-US",
            "timezone_id": "Asia/Tokyo",
        }

        if STORAGE_STATE and Path(
            STORAGE_STATE
        ).exists():
            context_args["storage_state"] = (
                STORAGE_STATE
            )

            log(
                f"🔐 使用 storage_state: "
                f"{STORAGE_STATE}"
            )

        context = await browser.new_context(
            **context_args
        )

        page = await context.new_page()

        page.set_default_timeout(15000)

        results = []

        for server_id in SERVER_IDS:
            try:
                result = await renew_server(
                    page,
                    server_id
                )

                results.append(
                    (server_id, result)
                )

            except Exception as e:
                log(
                    f"❌ Server {server_id} "
                    f"发生异常: {e}"
                )

                await save_debug(
                    page,
                    server_id,
                    "exception"
                )

                results.append(
                    (server_id, False)
                )

        await context.close()
        await browser.close()

    log("")
    log("=" * 60)
    log("处理结果")
    log("=" * 60)

    success_count = 0

    for server_id, result in results:
        status = "成功" if result else "失败"

        if result:
            success_count += 1

        log(
            f"{server_id}: {status}"
        )

    log(
        f"完成: {success_count}/"
        f"{len(results)}"
    )

    if success_count != len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

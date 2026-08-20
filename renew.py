#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Falix Renew Pro v2

当前 Falix Timer 自动检查/续期程序。

功能：
- Playwright Chromium
- Falix /timer?id=SERVER_ID
- 自动读取 Timer
- 低于阈值才尝试 Add Time
- 多服务器
- storage_state 登录
- CAPTCHA / Turnstile 检测
- Telegram 通知
- Screenshot + HTML 调试
- GitHub Actions 兼容

环境变量：

FALIX_SERVER_IDS
    服务器 ID，多个用逗号分隔

FALIX_STORAGE_STATE
    storage_state JSON 文件

RENEW_BELOW_MINUTES
    Timer 小于多少分钟时才尝试续期
    默认 20

HEADLESS
    true / false

TG_BOT_TOKEN
TG_CHAT_ID
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# Config
# ============================================================

BASE_URL = "https://client.falixnodes.net"

TIMER_URL = (
    BASE_URL +
    "/timer?id={}"
)

DEBUG_DIR = Path("debug_output")

DEBUG_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

SERVER_IDS = [
    x.strip()
    for x in os.getenv(
        "FALIX_SERVER_IDS",
        ""
    ).split(",")
    if x.strip()
]

STORAGE_STATE = os.getenv(
    "FALIX_STORAGE_STATE",
    "falix_state.json",
).strip()

RENEW_BELOW_MINUTES = int(
    os.getenv(
        "RENEW_BELOW_MINUTES",
        "20",
    )
)

HEADLESS = (
    os.getenv(
        "HEADLESS",
        "true",
    ).lower()
    == "true"
)

TG_BOT_TOKEN = os.getenv(
    "TG_BOT_TOKEN",
    "",
).strip()

TG_CHAT_ID = os.getenv(
    "TG_CHAT_ID",
    "",
).strip()


# ============================================================
# Logging
# ============================================================

def now():
    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def log(message):
    print(
        f"[{now()}] {message}",
        flush=True,
    )


def safe_name(server_id):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        server_id,
    )


# ============================================================
# Debug
# ============================================================

async def save_debug(
    page,
    server_id,
    name,
):
    prefix = (
        DEBUG_DIR /
        f"{safe_name(server_id)}_{name}"
    )

    try:
        await page.screenshot(
            path=str(
                prefix.with_suffix(".png")
            ),
            full_page=True,
        )

        log(
            f"📸 Screenshot: "
            f"{prefix}.png"
        )

    except Exception as e:
        log(
            f"⚠️ Screenshot 失败: {e}"
        )

    try:
        html = await page.content()

        prefix.with_suffix(
            ".html"
        ).write_text(
            html,
            encoding="utf-8",
        )

        log(
            f"📄 HTML: "
            f"{prefix}.html"
        )

    except Exception as e:
        log(
            f"⚠️ HTML 保存失败: {e}"
        )


# ============================================================
# Telegram
# ============================================================

def send_tg(message):
    if not TG_BOT_TOKEN:
        return

    if not TG_CHAT_ID:
        return

    url = (
        "https://api.telegram.org/bot"
        f"{TG_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            data={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )

        if not response.ok:
            log(
                "⚠️ Telegram 返回异常: "
                f"{response.status_code}"
            )

    except Exception as e:
        log(
            f"⚠️ Telegram 发送失败: {e}"
        )


# ============================================================
# Time parser
# ============================================================

def parse_time(text):
    if not text:
        return None

    text = text.strip().lower()

    # HH:MM:SS
    match = re.search(
        r"\b(\d{1,3}):(\d{2}):(\d{2})\b",
        text,
    )

    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))

        return (
            hours * 3600
            + minutes * 60
            + seconds
        )

    # MM:SS
    match = re.search(
        r"\b(\d{1,3}):(\d{2})\b",
        text,
    )

    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))

        return (
            minutes * 60
            + seconds
        )

    # 1h 20m 30s
    hours = re.search(
        r"(\d+)\s*h",
        text,
    )

    minutes = re.search(
        r"(\d+)\s*m",
        text,
    )

    seconds = re.search(
        r"(\d+)\s*s",
        text,
    )

    if hours or minutes or seconds:
        return (
            (
                int(hours.group(1))
                if hours
                else 0
            )
            * 3600
            +
            (
                int(minutes.group(1))
                if minutes
                else 0
            )
            * 60
            +
            (
                int(seconds.group(1))
                if seconds
                else 0
            )
        )

    return None


# ============================================================
# Page text
# ============================================================

async def get_body_text(page):
    try:
        return await page.locator(
            "body"
        ).inner_text(
            timeout=5000
        )
    except Exception:
        return ""


async def get_remaining_seconds(page):
    text = await get_body_text(page)

    if not text:
        return None

    clean = re.sub(
        r"\s+",
        " ",
        text,
    )

    log(
        "📄 页面文本: "
        + clean[:600]
    )

    # HH:MM:SS
    values = re.findall(
        r"\b\d{1,3}:\d{2}:\d{2}\b",
        text,
    )

    for value in values:
        seconds = parse_time(value)

        if (
            seconds is not None
            and seconds <= 7 * 24 * 3600
        ):
            return seconds

    # MM:SS
    values = re.findall(
        r"\b\d{1,3}:\d{2}\b",
        text,
    )

    for value in values:
        seconds = parse_time(value)

        if (
            seconds is not None
            and seconds <= 24 * 3600
        ):
            return seconds

    return None


def format_seconds(seconds):
    if seconds is None:
        return "UNKNOWN"

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    secs = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


# ============================================================
# CAPTCHA detection
# ============================================================

async def detect_captcha(page):
    patterns = [
        "captcha",
        "turnstile",
        "verify you are human",
        "verification",
        "cloudflare",
        "challenge",
    ]

    text = (
        await get_body_text(page)
    ).lower()

    for pattern in patterns:
        if pattern in text:
            return True

    selectors = [
        'iframe[src*="turnstile"]',
        'iframe[src*="captcha"]',
        '[class*="captcha"]',
        '[id*="captcha"]',
    ]

    for selector in selectors:
        try:
            if await page.locator(
                selector
            ).count():
                return True
        except Exception:
            pass

    return False


# ============================================================
# Login detection
# ============================================================

async def check_login(page):
    url = page.url.lower()

    if (
        "/login" in url
        or "/auth" in url
    ):
        return False

    text = (
        await get_body_text(page)
    ).lower()

    login_words = [
        "log in",
        "login",
        "sign in",
    ]

    for word in login_words:
        if word in text:
            # 如果页面明显出现 Timer，
            # 则不能简单判定为登录失效
            if (
                "timer" not in text
                and "server" not in text
            ):
                return False

    return True


# ============================================================
# Add Time
# ============================================================

async def find_add_time_button(page):

    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*Add\s+Time\s*$",
                re.I,
            ),
        ),

        page.get_by_text(
            re.compile(
                r"^\s*Add\s+Time\s*$",
                re.I,
            ),
        ),

        page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"Add\s*Time",
                re.I,
            )
        ),
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


async def click_add_time(
    page,
    server_id,
):
    button = (
        await find_add_time_button(page)
    )

    if button is None:
        log(
            "❌ 找不到 Add Time"
        )

        await save_debug(
            page,
            server_id,
            "add_time_missing",
        )

        return False

    try:
        await button.scroll_into_view_if_needed()

        await page.wait_for_timeout(
            1000
        )

        log(
            "🖱️ 点击 Add Time"
        )

        await button.click(
            timeout=10000
        )

        return True

    except Exception as e:
        log(
            f"❌ Add Time 点击失败: {e}"
        )

        await save_debug(
            page,
            server_id,
            "click_error",
        )

        return False


# ============================================================
# Wait for timer
# ============================================================

async def wait_timer_change(
    page,
    server_id,
    before,
    timeout=40,
):
    end_time = (
        time.time()
        + timeout
    )

    while time.time() < end_time:
        await page.wait_for_timeout(
            2000
        )

        current = (
            await get_remaining_seconds(
                page
            )
        )

        if current is None:
            continue

        log(
            "⏱️ Timer: "
            f"{format_seconds(before)}"
            " -> "
            f"{format_seconds(current)}"
        )

        if before is None:
            if current > 0:
                return True

        elif current > before + 30:
            return True

    return False


# ============================================================
# One server
# ============================================================

async def process_server(
    page,
    server_id,
):
    log("")
    log("=" * 70)
    log(
        f"🚀 开始处理服务器: "
        f"{server_id}"
    )
    log("=" * 70)

    url = TIMER_URL.format(
        server_id
    )

    log(
        f"🌐 {url}"
    )

    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

    except PlaywrightTimeoutError:
        log(
            "⚠️ 页面加载超时，继续检查"
        )

    except Exception as e:
        log(
            f"❌ 打开页面失败: {e}"
        )

        await save_debug(
            page,
            server_id,
            "goto_error",
        )

        return False

    await page.wait_for_timeout(
        3000
    )

    # Login
    if not await check_login(page):
        log(
            "❌ Falix 登录状态失效"
        )

        await save_debug(
            page,
            server_id,
            "login_required",
        )

        send_tg(
            "❌ Falix 登录状态失效\n"
            f"Server ID: {server_id}"
        )

        return False

    # CAPTCHA
    if await detect_captcha(page):
        log(
            "🛡️ 检测到 CAPTCHA / Turnstile"
        )

        await save_debug(
            page,
            server_id,
            "captcha",
        )

        send_tg(
            "⚠️ Falix 检测到 CAPTCHA\n"
            f"Server ID: {server_id}\n"
            "本轮没有尝试绕过验证。"
        )

        return False

    before = (
        await get_remaining_seconds(
            page
        )
    )

    log(
        "⏱️ 当前 Timer: "
        f"{format_seconds(before)}"
    )

    # Timer 足够，不续期
    if before is not None:
        threshold = (
            RENEW_BELOW_MINUTES
            * 60
        )

        if before > threshold:
            log(
                "✅ Timer 尚未低于阈值"
            )

            return True

    # Add Time
    button = (
        await find_add_time_button(page)
    )

    if button is None:
        log(
            "❌ 当前页面没有 Add Time"
        )

        await save_debug(
            page,
            server_id,
            "button_missing",
        )

        send_tg(
            "❌ Falix Add Time 按钮异常\n"
            f"Server ID: {server_id}"
        )

        return False

    log(
        "✅ 找到 Add Time"
    )

    # Click
    clicked = await click_add_time(
        page,
        server_id,
    )

    if not clicked:
        return False

    await page.wait_for_timeout(
        2000
    )

    # 点击后可能出现验证
    if await detect_captcha(page):
        log(
            "🛡️ 点击后出现 CAPTCHA"
        )

        await save_debug(
            page,
            server_id,
            "captcha_after_click",
        )

        send_tg(
            "⚠️ Falix 点击 Add Time "
            "后出现 CAPTCHA\n"
            f"Server ID: {server_id}"
        )

        return False

    # Verify
    success = await wait_timer_change(
        page,
        server_id,
        before,
    )

    after = (
        await get_remaining_seconds(
            page
        )
    )

    if success:
        log(
            "🎉 续期成功"
        )

        log(
            "⏱️ 新 Timer: "
            f"{format_seconds(after)}"
        )

        send_tg(
            "✅ Falix 续期成功\n"
            f"Server ID: {server_id}\n"
            f"Timer: {format_seconds(after)}"
        )

        await save_debug(
            page,
            server_id,
            "success",
        )

        return True

    log(
        "❌ 点击后 Timer 没有增加"
    )

    await save_debug(
        page,
        server_id,
        "renew_failed",
    )

    send_tg(
        "❌ Falix 续期失败\n"
        f"Server ID: {server_id}\n"
        f"Before: {format_seconds(before)}\n"
        f"After: {format_seconds(after)}"
    )

    return False


# ============================================================
# Main
# ============================================================

async def main():

    if not SERVER_IDS:
        log(
            "❌ 没有设置 FALIX_SERVER_IDS"
        )

        sys.exit(1)

    log(
        "======================================"
    )

    log(
        "Falix Renew Pro v2"
    )

    log(
        f"Servers: {len(SERVER_IDS)}"
    )

    log(
        "Threshold: "
        f"{RENEW_BELOW_MINUTES} minutes"
    )

    log(
        f"Headless: {HEADLESS}"
    )

    log(
        "======================================"
    )

    if not Path(
        STORAGE_STATE
    ).exists():
        log(
            "❌ storage_state 不存在:"
        )

        log(
            f"   {STORAGE_STATE}"
        )

        log(
            "请先运行 generate_storage.py"
        )

        sys.exit(1)

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            storage_state=STORAGE_STATE,
            viewport={
                "width": 1440,
                "height": 1000,
            },
            locale="en-US",
            timezone_id="Asia/Tokyo",
        )

        page = await context.new_page()

        page.set_default_timeout(
            15000
        )

        results = []

        for server_id in SERVER_IDS:

            try:
                result = (
                    await process_server(
                        page,
                        server_id,
                    )
                )

            except Exception as e:
                log(
                    f"❌ 未处理异常: {e}"
                )

                await save_debug(
                    page,
                    server_id,
                    "exception",
                )

                send_tg(
                    "💥 Falix 脚本异常\n"
                    f"Server ID: {server_id}\n"
                    f"Error: {e}"
                )

                result = False

            results.append(
                (
                    server_id,
                    result,
                )
            )

        await context.close()

        await browser.close()

    log("")
    log("=" * 70)
    log("处理结果")
    log("=" * 70)

    success = 0

    for server_id, result in results:

        status = (
            "✅ OK"
            if result
            else "❌ FAILED"
        )

        log(
            f"{server_id}: {status}"
        )

        if result:
            success += 1

    log(
        f"成功: {success}/"
        f"{len(results)}"
    )

    if success != len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

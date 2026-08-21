# Falix Renew Pro v2

> 自动检测并续期 **Falix** 免费服务器的工具（通过 Falix `/timer` 接口完成续期）。
>
> 项目仓库：[weikkadd/Falix-Renew-Pro-v2](https://github.com/weikkadd/Falix-Renew-Pro-v2)

---

## ✨ 功能特性

- 🤖 基于 **Playwright + Chromium** 浏览器自动化
- ⏱️ 自动读取服务器 **Timer**（剩余时间），**低于阈值才执行 `Add Time` 续期**
- 🖥️ 支持**多台服务器**批量续期
- 🔐 使用 `storage_state` 复用登录会话，**免去每次输入账号密码**
- 🛡️ 自动检测 **CAPTCHA / Turnstile** 人机验证
- 📢 可选 **Telegram Bot** 推送续期结果
- 🐛 自动保存 **截图 + 页面 HTML** 调试文件
- ☁️ 与 **GitHub Actions** 完全兼容，支持**定时自动运行**

---

## 📌 目录

- [工作原理](#-工作原理)
- [部署到 GitHub Actions（推荐）](#-部署到-github-actions推荐)
  - [1. 生成登录会话 storage_state](#1-生成登录会话-storage_state)
  - [2. 配置 GitHub Secrets](#2-配置-github-secrets)
  - [3. 触发与验证运行](#3-触发与验证运行)
- [本地运行](#-本地运行)
- [环境变量说明](#-环境变量说明)
- [调试与日志](#-调试与日志)
- [注意事项](#-注意事项)
- [许可证](#-许可证)

---

## 🧠 工作原理

1. 程序从环境变量中读取 **服务器 ID 列表** 与 **登录会话文件（storage_state）**。
2. 使用 Playwright 打开 Falix 客户端页面：`https://client.falixnodes.net/timer?id=SERVER_ID`。
3. 自动读取当前页面的 **剩余时间（Timer）**。
4. 若剩余时间 **低于阈值 `RENEW_BELOW_MINUTES`**，自动点击 `Add Time` 完成续期。
5. 续期结果（成功 / 失败）可选通过 Telegram 推送。
6. 每次运行都会把**截图与页面 HTML** 保存到 `debug_output/`，便于排查问题。

---

## ☁️ 部署到 GitHub Actions（推荐）

项目已内置工作流 `.github/workflows/falix-renew.yml`，默认**每 6 小时自动运行一次**，也支持**手动触发**。

### 1. 生成登录会话 storage_state

续期依赖一个**已登录的 Falix 会话**，程序通过 Playwright 的 `storage_state`（登录状态 JSON）登录，**而不是每次输入账号密码**。

> ⚠️ **重要**：当前仓库**未包含**生成该文件的脚本（`generate_storage.py`），需要你自行生成。

**方式 A：本地用 Playwright 导出**

1. 安装依赖并写一个导出脚本（核心代码如下）：
   ```python
   import asyncio
   from playwright.async_api import async_playwright

   async def main():
       async with async_playwright() as p:
           browser = await p.chromium.launch(headless=False)
           context = await browser.new_context()
           page = await context.new_page()
           await page.goto("https://client.falixnodes.net")
           input("请在浏览器中登录 Falix，登录完成后按回车...")
           await context.storage_state(path="falix_state.json")
           await browser.close()

   asyncio.run(main())
   ```
2. 运行脚本，在弹出的浏览器中完成 Falix 登录，回车后即生成 `falix_state.json`。

**方式 B：手动构造**

在浏览器登录 Falix 后，按 Playwright `storage_state` 的 JSON 格式（`{"cookies": [...], "origins": [...]}`）手动填入站点 Cookie 等信息。

**对生成好的 `falix_state.json` 进行 Base64 编码：**

```bash
# Linux / macOS
base64 -w 0 falix_state.json

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("falix_state.json"))
```

把输出的 **Base64 字符串**保存好，下一步要用。

### 2. 配置 GitHub Secrets

进入仓库：**Settings → Secrets and variables → Actions → New repository secret**，依次添加：

| Secret 名称 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `FALIX_SERVER_IDS` | ✅ | 服务器 ID，多个用**英文逗号**分隔 | `123456,789012` |
| `FALIX_STORAGE_STATE_B64` | ✅ | 登录会话 JSON 的 **Base64 编码** | `eyJjb29raWVzIjpbXX0=`（很长） |
| `TG_BOT_TOKEN` | ⭕ | Telegram Bot Token（用于通知） | `123456:ABC-xxx` |
| `TG_CHAT_ID` | ⭕ | 接收通知的 Telegram 聊天/群组 ID | `-100123456789` |

> 💡 **命名必须一字不差**。工作流会校验 `FALIX_SERVER_IDS` 与 `FALIX_STORAGE_STATE_B64` 是否存在，缺失会直接报错终止。

### 3. 触发与验证运行

- **定时运行**：工作流默认 `cron: "17 */6 * * *"`（每 6 小时的第 17 分钟）。
- **手动运行**：**Actions → Falix Renew Pro → Run workflow**，立即触发一次。

运行结束后：

- 在 **Summary** 标签页查看本次运行结果。
- 在 **Artifacts** 下载调试截图 / HTML（**保留 7 天**）。

---

## 🖥️ 本地运行

```bash
# 1. 安装 Python 依赖
python -m pip install -r requirements.txt

# 2. 安装 Playwright Chromium（含系统依赖）
python -m playwright install --with-deps chromium

# 3. 设置环境变量并运行
#    （Windows 用 set，Linux / macOS 用 export）
set FALIX_SERVER_IDS=123456,789012
set FALIX_STORAGE_STATE=falix_state.json
set TG_BOT_TOKEN=your_bot_token
set TG_CHAT_ID=your_chat_id

python renew.py
```

---

## ⚙️ 环境变量说明

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `FALIX_SERVER_IDS` | ✅ | — | 服务器 ID，多个用英文逗号分隔 |
| `FALIX_STORAGE_STATE` | ✅ | `falix_state.json` | storage_state 登录会话 JSON 文件路径 |
| `RENEW_BELOW_MINUTES` | ❌ | `20` | Timer 低于该分钟数时才尝试续期 |
| `HEADLESS` | ❌ | `true` | 是否以无头模式运行浏览器 |
| `TG_BOT_TOKEN` | ❌ | 空 | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | 空 | Telegram 接收通知的聊天 ID |

> 在 GitHub Actions 中，`FALIX_STORAGE_STATE` 由工作流根据 `FALIX_STORAGE_STATE_B64` 自动解码恢复，**无需手动配置**。

---

## 🐛 调试与日志

- 运行日志直接输出到控制台 / GitHub Actions 日志。
- 每次运行会在 `debug_output/` 生成**截图**与**页面 HTML**。
- GitHub Actions 会把调试文件作为 **Artifact** 上传，可用于排查：
  - **登录状态失效**：`storage_state` 过期 → 需重新登录并重新生成 `FALIX_STORAGE_STATE_B64`。
  - **CAPTCHA / Turnstile 拦截**：无法自动通过，检测到会通过 Telegram 告警并终止本次续期。

---

## ⚠️ 注意事项

- 🔑 **登录会话会过期**。出现"登录状态失效"日志时，需重新生成 `FALIX_STORAGE_STATE_B64`。
- 🛡️ **CAPTCHA / Turnstile** 无法自动绕过，需人工处理。
- 📏 免费服务器续期有平台规则限制，请**合理设置续期阈值与频率**，避免触发风控。
- 💾 GitHub Actions 免费额度对个人仓库通常足够；调试 Artifact 默认保留 7 天，注意及时清理。

---

## 📄 许可证

本项目以 **README.md** 原文为准，具体授权信息请参考仓库的 LICENSE 或项目发布说明。

---

*文档版本：v2 · 最后更新：2026-08-21*

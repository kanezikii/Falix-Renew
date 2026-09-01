# Falix Renew

> 自动检测并续期 **Falix** 免费服务器的工具（通过 Falix `/timer` 接口完成续期）。
>
> 网站：https://client.falixnodes.net/

---

## ✨ 功能特性

- 🤖 基于 **CloakBrowser**（stealth Chromium）+ 代理，**绕过 Cloudflare Managed Challenge** 指纹检测
- ⏱️ 自动读取服务器 **Timer**（剩余时间），**低于阈值才执行 `Add Time` 续期**
- 🖥️ 支持**多台服务器**批量续期
- 🔐 使用 `storage_state` 复用登录会话，**免去每次输入账号密码**
- 🛡️ 自动检测 **CAPTCHA / Turnstile / Managed Challenge** 并尝试自动通过（坐标点击 iframe + 拟人化鼠标轨迹）
- 🌐 支持多格式代理节点（hysteria2 / hy2 / tuic / vless / vmess），由工作流自动启动 sing-box 转本地 SOCKS5
- 📢 可选 **Telegram Bot** 推送续期结果
- 🐛 自动保存 **截图 + 页面 HTML** 调试文件
- ☁️ 与 **GitHub Actions** 完全兼容，**每 6 小时自动运行一次**

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
2. 用 **CloakBrowser**（stealth Chromium，已注入绕过 CF 指纹检测的 patches）打开 Falix 客户端页面：`https://client.falixnodes.net/timer?id=SERVER_ID`。
3. 如果触发 Cloudflare **"Just a moment..." Managed Challenge**，脚本会等待自动评估通过；如果出现 visible checkbox（Turnstile widget），会坐标点击 iframe checkbox 模拟人类点击。
4. 自动读取当前页面的 **剩余时间（Timer）**。
5. 若剩余时间 **低于阈值 `RENEW_BELOW_MINUTES`**，自动点击 `Add Time` 完成续期。
6. 续期结果（成功 / 失败）可选通过 Telegram 推送。
7. 每次运行都会把**截图与页面 HTML** 保存到 `debug_output/`，便于排查问题。

---

## ☁️ 部署到 GitHub Actions（推荐）

项目已内置工作流 `.github/workflows/falix-renew.yml`，默认**每 6 小时自动运行一次**（`cron: "17 */6 * * *"`），也支持**手动触发**。

### 1. 生成登录会话 storage_state

续期依赖一个**已登录的 Falix 会话**，程序通过 Playwright 的 `storage_state`（登录状态 JSON）登录，**而不是每次输入账号密码**。

> ⚠️ **重要**：当前仓库**未包含**生成该文件的脚本（`generate_storage.py`），需要你自行生成。下面提供 3 种方法。

#### 方式 A：浏览器扩展 Cookie Manager Pro（最简单）

1. 在 Chrome/Edge 安装 **Cookie Manager Pro** 扩展。
2. 打开 https://client.falixnodes.net 并**登录成功**。
3. 在登录后的页面点击 Cookie Manager Pro 图标 → **格式选 JSON** → **Export / 复制**。
4. 把导出的 JSON 数组（`[{...}, {...}]` 格式）贴到一个文件 `falix_state.json` 里。

工作流会自动识别这种裸数组格式并转换成 Playwright 兼容的 `{"cookies": [...], "origins": []}` 格式（包括 `expirationDate → expires`、`sameSite` 大小写规范化、移除 `hostOnly/storeId/session` 等字段）。

#### 方式 B：本地用 Playwright 导出

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

#### 方式 C：手动构造

在浏览器登录 Falix 后，按 Playwright `storage_state` 的 JSON 格式（`{"cookies": [...], "origins": [...]}`）手动填入站点 Cookie 等信息。

#### 把生成好的 `falix_state.json` 进行 Base64 编码

```bash
# Linux / macOS
base64 -w 0 falix_state.json

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("falix_state.json"))
```

> 💡 工作流兼容**两种** secret 内容：**Base64 编码的 JSON** 或 **直接 JSON 明文**。Base64 编码推荐但非强制。如果你用 Cookie Manager Pro 直接复制 JSON 数组，也可以直接粘贴到 Secret 里（工作流会自动判断并解码/包装）。

把输出的 **Base64 字符串**或**JSON 明文**保存好，下一步要用。

### 2. 配置 GitHub Secrets

进入仓库：**Settings → Secrets and variables → Actions → New repository secret**，依次添加：

| Secret 名称 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `FALIX_SERVER_IDS` | ✅ | 服务器 ID，多个用**英文逗号**分隔 | `123456,789012` |
| `FALIX_STORAGE_STATE` | ✅ | 登录会话 JSON 的 **Base64 编码**或**JSON 明文** | `eyJjb29raWVzIjogW3siZG9tYWluIjog...` |
| `PROXY_URI` | ⭕ | 代理节点链接（多格式），用于绕过 Cloudflare 风控 | `hysteria2://password@host:port?sni=...&insecure=1` |
| `TG_BOT_TOKEN` | ⭕ | Telegram Bot Token（用于通知） | `123456:ABC-xxx` |
| `TG_CHAT_ID` | ⭕ | 接收通知的 Telegram 聊天/群组 ID | `-100123456789` |

> 💡 **命名必须一字不差**。工作流会校验 `FALIX_SERVER_IDS` 与 `FALIX_STORAGE_STATE` 是否存在，缺失会直接报错终止。

#### PROXY_URI 格式说明

工作流自动识别以下 5 种协议前缀：

| 协议 | 链接格式示例 |
|------|-------------|
| **hysteria2**（推荐） | `hysteria2://password@host:port?sni=example.com&insecure=1` |
| **hy2**（hysteria2 别名） | `hy2://password@host:port?sni=example.com&insecure=1` |
| **tuic** | `tuic://uuid:password@host:port?sni=example.com&insecure=0&alpn=h3` |
| **vless** | `vless://uuid@host:port?security=tls&sni=example.com` |
| **vmess** | `vmess://base64编码的JSON...` |

在 v2rayN / NekoBox / Clash Verge 等客户端中，右键节点 → **分享 / 复制链接** 即可得到上述格式。

**URI 参数说明**：

| 参数 | 必填 | 说明 |
|------|------|------|
| `sni` | ❌ | TLS SNI，缺省时使用 host |
| `insecure` / `allowInsecure` | ❌ | 是否禁用证书校验，`1`=禁用（自签证书用），`0`=校验 |
| `alpn` | ❌ | ALPN 协议（tuic 常用 `h3`） |

> ⚠️ **不配 `PROXY_URI` 会怎样**：GitHub Actions 裸 IP（数据中心 IP）会被 Cloudflare 风控评分极低，Falix `/timer?id=...` 页面会触发 CF "Just a moment..." Managed Challenge。Managed Challenge 完全靠浏览器指纹自动评估，headless Chromium 大概率被识别为 bot 无法通过。**强烈建议配代理**，最佳是住宅代理（residential IP）。

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

# 2. 安装 Playwright Chromium（含系统依赖）+ CloakBrowser stealth Chromium
python -m playwright install --with-deps chromium
python -c "from cloakbrowser import ensure_binary; ensure_binary()"

# 3. 设置环境变量并运行
#    （Windows 用 set，Linux / macOS 用 export）
set FALIX_SERVER_IDS=123456,789012
set FALIX_STORAGE_STATE=falix_state.json
set TG_BOT_TOKEN=your_bot_token
set TG_CHAT_ID=your_chat_id

# 4. (可选) 设置代理 — 本地直接走 socks5
set ALL_PROXY=socks5h://127.0.0.1:1080

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
| `PROXY_URI` | ❌ | 空 | 代理节点链接（hysteria2/hy2/tuic/vless/vmess），仅用于 GitHub Actions workflow 启动 sing-box；本地运行直接设 `ALL_PROXY` |
| `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` | ❌ | 空 | 标准 socks5/http 代理 URL（如 `socks5h://127.0.0.1:1080`），CloakBrowser 会自动走该代理 |
| `TG_BOT_TOKEN` | ❌ | 空 | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | 空 | Telegram 接收通知的聊天 ID |

> 在 GitHub Actions 中，`FALIX_STORAGE_STATE` 由工作流根据 `FALIX_STORAGE_STATE` secret 自动解码/转换恢复，**无需手动配置**。

---

## 🐛 调试与日志

- 运行日志直接输出到控制台 / GitHub Actions 日志。
- 每次运行会在 `debug_output/` 生成**截图**与**页面 HTML**，文件名格式 `<server_id>_<event>.png/.html`：
  - `*_captcha_before.*` — 检测到 CAPTCHA 时的初始页面状态
  - `*_captcha_passed.*` — CAPTCHA 自动通过后的页面（成功）
  - `*_captcha_failed.*` — CAPTCHA 自动通过失败后的最终页面
  - `*_captcha_after_click_before.*` — 点击 Add Time 后出现 CAPTCHA 时的状态
  - `*_login_required.*` — 登录失效时
  - `*_success.*` — 续期成功
  - `*_renew_failed.*` — 续期失败（点击 Add Time 后 Timer 没增加）
  - `*_exception.*` — 脚本异常
- GitHub Actions 会把调试文件作为 **Artifact** 上传，可用于排查：
  - **登录状态失效**：`storage_state` 过期 → 需重新登录并重新生成 `FALIX_STORAGE_STATE`。
  - **CF Managed Challenge 卡住**：通常是代理不可用或代理 IP 被风控；切换到住宅代理通常能解决。
  - **续期失败但无错误**：Falix 平台规则限制（如冷却期内不能续），可调整 `RENEW_BELOW_MINUTES` 阈值。

---

## ⚠️ 注意事项

- 🔑 **登录会话会过期**。出现"登录状态失效"日志时，需重新生成 `FALIX_STORAGE_STATE`。
- 🛡️ **Cloudflare Managed Challenge** 在 GitHub Actions headless 环境下难以自动通过；CloakBrowser + 代理是当前最佳绕过方案，但不保证 100% 成功。若长期无法通过，考虑：
  - 切换为**住宅代理**（residential IP，CF 风控评分更低）
  - 改为**半自动模式**：脚本检测到 managed challenge 即发 Telegram 告警，你在自己浏览器手动续期
- 📏 免费服务器续期有平台规则限制，请**合理设置续期阈值与频率**，避免触发风控。
- 💾 GitHub Actions 免费额度对个人仓库通常足够；调试 Artifact 默认保留 7 天，注意及时清理。

---

## 📄 许可证

本项目以 **README.md** 原文为准，具体授权信息请参考仓库的 LICENSE 或项目发布说明。

---

*文档版本：v2.1 · 最后更新：2026-08-29*

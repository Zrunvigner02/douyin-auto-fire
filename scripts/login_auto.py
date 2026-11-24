"""扫码登录并把登录状态保存到 storage-state.json（自动检测，无需按回车）。

上游的 scripts/login.py 需要在终端里按 Enter 确认登录完成。本脚本把这一步换成
轮询检测：一旦页面上"扫码登录"提示消失、且出现已登录标志（私信入口），就自动
保存凭证并退出。适合在没有人盯着终端的环境里运行。

用法：
    .venv/bin/python scripts/login_auto.py
    .venv/bin/python scripts/login_auto.py --timeout 600

登录成功后凭证写入 storage-state.json（已被 .gitignore 覆盖）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

from app.selectors import LOGIN_MARKERS, LOGIN_REQUIRED_MARKERS


DOUYIN_URL = "https://www.douyin.com/"
STATE_PATH = PROJECT_ROOT / "storage-state.json"
POLL_INTERVAL_SECONDS = 2


async def _visible(page, selectors: tuple[str, ...], timeout_ms: int = 800) -> bool:
    per = max(200, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=per)
            return True
        except Exception:
            continue
    return False


async def _open_login(page) -> None:
    for text in ("登录", "扫码登录"):
        node = page.get_by_text(text, exact=True)
        try:
            if await node.count():
                await node.first.click(timeout=8_000)
                await page.wait_for_timeout(1_000)
        except Exception:
            pass


async def _logged_in(page) -> bool:
    """已登录 = 登录提示消失 且 出现私信入口。"""
    if await _visible(page, LOGIN_REQUIRED_MARKERS):
        return False
    return await _visible(page, LOGIN_MARKERS)


async def run(timeout_seconds: int) -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        await page.goto(DOUYIN_URL, wait_until="domcontentloaded", timeout=60_000)
        await _open_login(page)

        print()
        print("=" * 62)
        print("  请在弹出的浏览器窗口里用抖音 App 扫码登录。")
        print("  登录成功后本脚本会自动检测到并退出，不需要按任何键。")
        print(f"  最多等待 {timeout_seconds} 秒。")
        print("=" * 62)
        print(flush=True)

        elapsed = 0
        while elapsed < timeout_seconds:
            await page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)
            elapsed += POLL_INTERVAL_SECONDS
            if await _logged_in(page):
                print(f"[✓] 检测到登录成功（用时 {elapsed} 秒）", flush=True)
                break
            if elapsed % 20 == 0:
                print(f"[…] 仍在等待扫码…（已等待 {elapsed} 秒）", flush=True)
        else:
            print("[✗] 等待超时，未检测到登录成功。请重试。", flush=True)
            await browser.close()
            return 1

        # 存到临时文件再原子替换，避免写一半留下损坏的凭证文件
        temporary = STATE_PATH.with_suffix(".json.tmp")
        await context.storage_state(path=str(temporary))
        temporary.replace(STATE_PATH)
        await browser.close()

    print(f"[✓] 登录状态已保存到 {STATE_PATH}", flush=True)
    print("[i] 该文件是登录凭证，等同于账号密码，请勿外传或提交到公开仓库。", flush=True)
    print(flush=True)
    print("下一步：运行只读脚本导出会话列表", flush=True)
    print("  .venv/bin/python scripts/list_chats.py --out chats.md", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="扫码登录并自动保存 storage-state.json")
    parser.add_argument("--timeout", type=int, default=300, help="等待扫码的秒数，默认 300")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.timeout))
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

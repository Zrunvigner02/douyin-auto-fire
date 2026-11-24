"""只读脚本：导出抖音私信会话列表（含火花状态），用于核对要续火花的目标。

本脚本只做三件事：打开浏览器、读取私信页面的会话列表、打印出来。
它**不会**输入任何文字、**不会**点击进入任何会话、**不会**发送任何消息。

用法：
    .venv/bin/python scripts/list_chats.py
    .venv/bin/python scripts/list_chats.py --out chats.md --scroll 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser import open_douyin, open_private_messages
from app.config import ConfigError, load_settings


# 与 app/douyin.py 保持一致的行选择器
ROW_SELECTOR = '[data-e2e="conversation-item"]'

# 逐行提取。选择器注释说明每个字段为什么这么取：
#   - 名字必须用 [class~=...]（整词匹配）。用 [class*=...] 会连它的父元素
#     conversationConversationItemtitleWrapper 一起命中，而父元素里混着火花
#     天数和时间，innerText 会变成"名字\n748\n刚刚"。
#   - 群的判别：群头像直接是 <img class="commonConversationIconnoDrag">，
#     好友头像则包在 .commonIMAvataravatarContainer 里。
EXTRACT_JS = """() => {
  const rows = document.querySelectorAll('[data-e2e="conversation-item"]');
  return Array.from(rows).map(row => {
    const nameEl  = row.querySelector('[class~="conversationConversationItemtitle"]');
    const streak  = row.querySelector('[class*="StreakstreakContainer"]');
    const streakT = row.querySelector('[class*="StreaknormalText"]');
    const timeEl  = row.querySelector('[class*="TagNextToTitletimeStr"]');
    const hintEl  = row.querySelector('[class*="ConversationItemHinttextBox"]');
    const descEl  = row.querySelector('[class*="ConversationItemDescleft"]');
    const isGroup = !!row.querySelector('[class*="commonConversationIconnoDrag"]')
                 || !row.querySelector('[class*="commonIMAvataravatarContainer"]');
    const muted   = !!row.querySelector('[class*="TagNextToTitlemuteIcon"]');
    return {
      name:   nameEl ? (nameEl.innerText || '').trim() : '',
      streak: streak ? (streak.innerText || '').trim() : '',
      streak_color: streakT ? getComputedStyle(streakT).color : '',
      time:   timeEl ? (timeEl.innerText || '').trim() : '',
      hint:   hintEl ? (hintEl.innerText || '').trim() : '',
      desc:   descEl ? (descEl.innerText || '').trim() : '',
      is_group: isGroup,
      muted: muted,
    };
  });
}"""


async def collect(page, max_scrolls: int) -> list[dict]:
    found: dict[str, dict] = {}
    stale_rounds = 0

    for round_index in range(max_scrolls + 1):
        rows = await page.evaluate(EXTRACT_JS)
        before = len(found)
        for entry in rows:
            name = entry.get("name")
            if not name or name in found:
                continue
            # 同一名字出现两次时，保留带火花信息的那条
            found[name] = entry

        gained = len(found) - before
        print(
            f"[i] 第 {round_index + 1} 轮: 本屏 {len(rows)} 行, 新增 {gained}, 累计 {len(found)}",
            file=sys.stderr,
        )

        stale_rounds = stale_rounds + 1 if gained == 0 else 0
        if stale_rounds >= 2:
            break
        if round_index < max_scrolls:
            try:
                first = page.locator(ROW_SELECTOR).first
                if await first.count():
                    await first.hover(timeout=2_000)
            except Exception:
                pass
            try:
                await page.mouse.wheel(0, 1_200)
            except Exception:
                pass
            await page.wait_for_timeout(900)

    return list(found.values())


def _spark_label(entry: dict) -> str:
    if not entry.get("streak"):
        return "—"
    return f"🔥 {entry['streak']}"


def _render_markdown(entries: list[dict]) -> str:
    with_spark = [e for e in entries if e.get("streak")]
    without = [e for e in entries if not e.get("streak")]
    friends = [e for e in entries if not e.get("is_group")]
    groups = [e for e in entries if e.get("is_group")]

    lines = [
        "# 抖音会话列表（只读导出）",
        "",
        f"共读到 **{len(entries)}** 个会话：私聊 {len(friends)}，群 {len(groups)}；"
        f"其中**带火花标识** {len(with_spark)} 个，无火花 {len(without)} 个。",
        "",
        "## 怎么用这份表",
        "",
        "1. 从下面挑出你要续火花的 16 个目标。",
        "2. 好友：`config 里填的 name` 一列原样复制即可。",
        "3. 群：**填基础名，不要带括号人数**。任务代码用 `名字(人数)` 全匹配，",
        "   填 `郑州 ai 交流群` 能匹配到 `郑州 ai 交流群(123)`，人数变了也不失配。",
        "4. `火花` 一列是左侧列表显示的火花天数；`提示` 一列是抖音给出的到期告警原文。",
        "",
        "## 带火花的会话（优先从中挑）",
        "",
        "| # | 类型 | 显示名 | 火花 | 最后消息 | 提示 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(
        sorted(with_spark, key=lambda e: (e.get("is_group"), e.get("name") or "")), 1
    ):
        kind = "群" if entry.get("is_group") else "好友"
        hint = (entry.get("hint") or entry.get("desc") or "").replace("|", "\\|")
        lines.append(
            f"| {index} | {kind} | `{entry['name']}` | {_spark_label(entry)} "
            f"| {entry.get('time') or ''} | {hint} |"
        )

    lines += [
        "",
        "## 无火花标识的会话",
        "",
        "| # | 类型 | 显示名 | 最后消息 | 提示 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(without, 1):
        kind = "群" if entry.get("is_group") else "好友"
        hint = (entry.get("hint") or entry.get("desc") or "").replace("|", "\\|")
        lines.append(
            f"| {index} | {kind} | `{entry['name']}` | {entry.get('time') or ''} | {hint} |"
        )

    groups_with = [e for e in groups]
    if groups_with:
        lines += [
            "",
            "## 群单独列出",
            "",
            "| # | 显示名 | 火花 | config 里填的 name |",
            "| --- | --- | --- | --- |",
        ]
        for index, entry in enumerate(groups_with, 1):
            lines.append(
                f"| {index} | `{entry['name']}` | {_spark_label(entry)} | `{entry['name']}` |"
            )

    lines.append("")
    return "\n".join(lines)


async def run(max_scrolls: int, out: Path | None) -> int:
    settings = load_settings()
    if not settings.storage_state and not settings.cookie:
        raise ConfigError(
            "没有找到登录凭证。请先运行 .venv/bin/python scripts/login_auto.py 扫码登录。"
        )

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)

    async with open_douyin(settings) as session:
        page = session.page
        await open_private_messages(page)
        entries = await collect(page, max_scrolls)

        screenshot = settings.artifacts_dir / "chats-snapshot.png"
        try:
            await page.screenshot(path=screenshot, full_page=False)
        except Exception:
            screenshot = None

    markdown = _render_markdown(entries)
    if out is not None:
        out.write_text(markdown, encoding="utf-8")
        print(f"[✓] Markdown 已写入: {out}")
        (out.with_suffix(".json")).write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[✓] 原始 JSON 已写入: {out.with_suffix('.json')}")
    else:
        print(markdown)
    if screenshot:
        print(f"[i] 页面截图: {screenshot}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读导出抖音会话列表（不发送任何消息）")
    parser.add_argument("--out", type=Path, help="输出 Markdown 文件路径")
    parser.add_argument("--scroll", type=int, default=15, help="最多滚动轮数，默认 15")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.scroll, args.out))
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

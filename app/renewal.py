"""判断私聊「今天双方是否已经互相对话过」，用于避免重复自动续火花。

只读：仅从已渲染的消息列表里读时间分隔符，不点任何按钮、不发消息。

判据：同一天里既有一条我发出的消息，也有一条对方发出的消息 → 视为已续上。
时间分隔符挂在每组最新一条上，所以从新往旧遍历时把最近一次看到的时间
向下传播给同组的旧消息（与 scripts/verify_sent.py 的做法一致）。
"""

from __future__ import annotations

import re

from playwright.async_api import Page

READ_JS = """() => {
  const list = document.querySelector('[class*="messageMessageListlist"]');
  if (!list) return [];
  return [...list.querySelectorAll('[data-index]')].map(el => ({
    me: !!el.querySelector('[class*="isFromMe"]'),
    time: ((el.querySelector('[class*="MessageBoxTimetimeLayout"]') || {}).innerText || '').trim(),
  }));
}"""


def is_today_label(label: str) -> bool:
    """把抖音的时间分隔符归类为「今天」。未知一律算否（保守，不跳过发送）。"""
    text = (label or "").strip()
    if not text:
        return False
    if text == "刚刚" or text.endswith("分钟前") or text.endswith("小时前"):
        return True
    if re.match(r"^今天\s*\d{1,2}:\d{2}$", text):
        return True
    if re.match(r"^\d{1,2}:\d{2}$", text):
        return True
    return False


async def renewed_today(page: Page) -> bool:
    """当前打开的私聊里，今天是否已双方都发过消息。"""
    try:
        rows = await page.evaluate(READ_JS)
    except Exception:
        return False
    if not isinstance(rows, list):
        return False

    current = ""
    mine_today = False
    theirs_today = False
    for row in rows:  # DOM 顺序 = 从新到旧
        if not isinstance(row, dict):
            continue
        label = str(row.get("time") or "").strip()
        if label:
            current = label
        if not is_today_label(current):
            continue
        if row.get("me"):
            mine_today = True
        else:
            theirs_today = True
        if mine_today and theirs_today:
            return True
    return False

"""只读诊断：备注改了、搜索不到人时，用名字的不同片段反查当前显示的联系人。

不点进任何会话、不输入任何消息、不写文件。

用法：
    .venv/bin/python scripts/find_target.py 旧备注A
    .venv/bin/python scripts/find_target.py 旧备注B 旧备注C 旧备注D

拿到结果后，把新名字填到 config.json 对应目标的 `aliases` 里即可，
**不要改 `name`**（name 是 history.json 账本身份，改了会导致重发/漏记）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser import open_douyin, open_private_messages
from app.config import load_settings
from app.douyin import first_visible
from app.selectors import SEARCH_INPUTS

# 搜索结果行；名字节点和 app/douyin.py 用的是同一组选择器。
DUMP_JS = """() => {
  const out = [];
  const rows = document.querySelectorAll(
    '[class*="SearchPanelitembox"], [class*="SearchPanelitem-box"], [class*="SearchPanelitem_box"]'
  );
  for (const row of rows) {
    const nameEl = row.querySelector(
      '[class*="SearchPanelitemtitle"], [class*="SearchPanelitemTitle"], [class*="SearchPanelitem_title"], [class*="SearchPanelitemname"], [class*="SearchPanelitemName"]'
    );
    const name = nameEl ? (nameEl.innerText || '').replace(/\\s+/g, '').trim() : '';
    const text = (row.innerText || '').replace(/\\n/g, ' ').trim().slice(0, 100);
    if (name || text) out.push({name, text});
  }
  return out;
}"""


def _queries(name: str) -> list[str]:
    """从全名递减到 2 字片段，兼顾前缀和后缀改名。"""
    out: list[str] = []
    candidates = [name] + [name[:n] for n in range(len(name) - 1, 1, -1)] + [
        name[-n:] for n in range(2, len(name))
    ]
    for query in candidates:
        query = query.strip()
        if len(query) >= 2 and query not in out:
            out.append(query)
    return out


async def search_one(page, query: str) -> list[dict]:
    search = await first_visible(page, SEARCH_INPUTS, 8_000)
    await search.click()
    await search.fill("")
    await search.fill(query)
    await page.wait_for_timeout(1_800)
    rows = await page.evaluate(DUMP_JS)
    return rows


async def run(names: list[str]) -> int:
    settings = load_settings()
    async with open_douyin(settings) as session:
        page = session.page
        await open_private_messages(page)
        for name in names:
            print(f"\n=== 目标 {name!r} ===", flush=True)
            seen: dict[str, str] = {}
            for query in _queries(name):
                try:
                    rows = await search_one(page, query)
                except Exception as exc:
                    print(f"  [{query!r}] 出错: {exc}", flush=True)
                    continue
                hits = [r for r in rows if r.get("name") or "发消息" in r.get("text", "")]
                if hits:
                    print(f"  [{query!r}] 命中 {len(hits)} 条", flush=True)
                    for item in hits[:8]:
                        shown = item["name"] or item["text"]
                        if shown not in seen:
                            seen[shown] = query
                            print(f"      {shown!r:<24} (由 {query!r} 搜到)", flush=True)
                else:
                    print(f"  [{query!r}] —", flush=True)
            if not seen:
                print("  没有任何搜索结果：可能已注销/删好友，或名字完全换了。", flush=True)
                print("  可运行 .venv/bin/python scripts/list_chats.py --out chats.md 导出当前会话再比对。", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读反查改名后的联系人，不发送任何消息")
    parser.add_argument("names", nargs="+", help="要查的旧名字（config.json 里的 name）")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.names))
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

"""只读采集：逐个打开目标会话 → 点头像进个人主页 → 读取抖音昵称 / 抖音号 / sec_uid。

不发送任何消息。结果写入 nicknames.json（含真实昵称，已 gitignore）。

用法：
    .venv/bin/python scripts/collect_nicknames.py
    .venv/bin/python scripts/collect_nicknames.py --only 好友01,好友02
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser import open_douyin, open_private_messages
from app.config import load_settings, load_task
from app.main import _open_target_with_aliases

PROFILE_JS = """() => {
  const h1 = document.querySelector('h1');
  const body = document.body.innerText || '';
  const m = body.match(/抖音号[：:]\\s*([^\\s]+)/);
  const title = document.title || '';
  const fromTitle = title.match(/^(.+?)的抖音/);
  return {
    nickname: (h1 && h1.innerText || '').trim() || (fromTitle ? fromTitle[1].trim() : ''),
    douyin_id: m ? m[1].trim() : '',
  };
}"""


def _is_group(name: str) -> bool:
    try:
        cfg = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
        return "群" in (cfg.get("_targets_legend", {}).get(name, ""))
    except Exception:
        return False


async def collect_one(session, target) -> dict:
    page = session.page
    base = {"name": target.name, "aliases": list(target.aliases)}
    await open_private_messages(page)
    await page.wait_for_timeout(500)

    try:
        matched = await _open_target_with_aliases(_chat(page), target, 0)
        base["matched"] = matched
    except Exception as exc:
        base["error"] = f"打不开会话: {exc}"
        return base

    before = len(session.context.pages)
    try:
        avatar = page.locator('[data-apm-action="个人页卡片"] span[role="listitem"]').first
        await avatar.click(timeout=5000)
        await page.wait_for_timeout(500)
    except Exception as exc:
        base["error"] = f"点不开头像: {exc}"
        return base

    for _ in range(30):
        if len(session.context.pages) > before:
            break
        await page.wait_for_timeout(300)
    if len(session.context.pages) <= before:
        base["error"] = "没有打开个人主页"
        return base

    profile = session.context.pages[-1]
    try:
        data: dict = {}
        for attempt in range(4):  # 个人主页偶尔重导航，整轮重试
            try:
                for _ in range(40):  # 异步渲染，最长等 ~16 秒
                    data = await profile.evaluate(PROFILE_JS)
                    if data.get("nickname") and data.get("douyin_id"):
                        break
                    await profile.wait_for_timeout(400)
                break
            except Exception:
                await profile.wait_for_timeout(1200)
        base.update(data)
        uid = re.search(r"/user/([^?/]+)", profile.url)
        base["sec_uid"] = uid.group(1) if uid else ""
        if not base.get("nickname"):
            base["error"] = "昵称未渲染"
    except Exception as exc:
        base["error"] = f"读取资料失败: {exc}"
    finally:
        try:
            await profile.close()
        except Exception:
            pass
    return base


def _chat(page):
    # 复用页面上的 DouyinChat；避免重复实现搜索逻辑
    from app.douyin import DouyinChat

    return DouyinChat(page, timeout_ms=10_000, confirm_timeout_ms=6_000)


async def run(only: list[str] | None) -> int:
    settings = load_settings()
    task = load_task(settings)
    targets = [t for t in task.targets if not _is_group(t.name)]
    if only:
        wanted = set(only)
        targets = [t for t in targets if t.name in wanted]

    results: list[dict] = []
    async with open_douyin(settings) as session:
        for index, target in enumerate(targets, 1):
            print(f"[{index}/{len(targets)}] {target.name} …", flush=True)
            result = await collect_one(session, target)
            results.append(result)
            print(
                f"   {result.get('nickname') or '—'}  "
                f"抖音号={result.get('douyin_id') or '—'}  "
                f"{result.get('error') or ''}",
                flush=True,
            )

    out = PROJECT_ROOT / "nicknames.json"
    merged: dict[str, dict] = {}
    if out.is_file():
        try:
            for item in json.loads(out.read_text(encoding="utf-8")):
                if item.get("name"):
                    merged[item["name"]] = item
        except Exception:
            pass
    for item in results:
        # 只成功的覆盖已有记录，避免把之前抓到的好数据冲掉
        if item.get("nickname") or item["name"] not in merged:
            merged[item["name"]] = item
    out.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[✓] 已写入 {out}")
    ok = sum(1 for r in results if r.get("nickname"))
    print(f"成功 {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="只读采集所有好友的抖音昵称")
    parser.add_argument("--only", help="只采集指定目标，逗号分隔（config 里的 name）")
    args = parser.parse_args()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    try:
        return asyncio.run(run(only))
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

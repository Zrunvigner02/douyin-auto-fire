"""复检：逐个打开 16 个会话，确认**今天**确实发过消息；可选自动补发漏掉的。

为什么需要它：任务脚本报"发送成功"依据的是发送时的页面状态（气泡出现、无重试标记）。
但那是**发送侧**的自我判断。本脚本做的是**接收侧**的独立复检 —— 回到每个聊天里，
看最新一条来自我的标记消息，其**时间分隔符**是不是今天。

判据来自实测的 DOM 结构（`[data-index]` 越小越新，时间分隔符挂在每个时间组的
最新那条上）：

    idx=1  我  TS=13分钟前   "13分钟前 叮！自动续火花"   ← 今天
    idx=8  我  TS=昨天 20:51                            ← 昨天

用法：
    .venv/bin/python scripts/verify_sent.py                # 只复检，打印报告
    .venv/bin/python scripts/verify_sent.py --fix          # 复检 + 自动补发漏掉的 + 重新复检
    .venv/bin/python scripts/verify_sent.py --json out.json
    .venv/bin/python scripts/verify_sent.py --only 目标A,目标B

退出码：0 = 全部确认；1 = 有遗漏；2 = 运行出错（如登录失效）。

注意：`--fix` 会**真实发送消息**给漏掉的好友/群。补发用的是与主任务完全相同的
消息内容，也会正确写入 history.json（防重复账本），不会造成重复发送。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser import AuthenticationError, RiskControlError, open_douyin, open_private_messages
from app.config import ConfigError, load_settings, load_task
from app.douyin import DouyinChat
from app.history import History
from app.main import _message_id
from app.models import Message, TaskConfig
from app.sender import send_message


# 在聊天页里找出"最新一条我发的标记消息"及其所属时间组的时间。
#
# [data-index] 的文档顺序 = 从新到旧。时间分隔符只挂在每个时间组的**最新**那条上，
# 所以对位置 hit 的消息，它的时间 = 位置 <= hit 的最近一条带时间的行（含 hit 自己）。
EXTRACT_JS = """(markers) => {
  const list = document.querySelector('[class*="messageMessageListlist"]');
  if (!list) return {error: 'no-message-list'};

  const rows = [...list.querySelectorAll('[data-index]')];
  if (!rows.length) return {error: 'no-rows'};

  const info = rows.map(el => ({
    fromMe: !!el.querySelector('[class*="isFromMe"]'),
    time: ((el.querySelector('[class*="MessageBoxTimetimeLayout"]') || {}).innerText || '').trim(),
    text: el.innerText || '',
  }));

  let hit = -1;
  for (let i = 0; i < info.length; i++) {
    if (!info[i].fromMe) continue;
    if (markers.some(m => info[i].text.includes(m))) { hit = i; break; }
  }
  if (hit < 0) return {found: false};

  let time = info[hit].time;
  if (!time) {
    for (let i = hit; i >= 0; i--) { if (info[i].time) { time = info[i].time; break; } }
  }
  if (!time) {
    for (let i = hit + 1; i < info.length; i++) { if (info[i].time) { time = info[i].time; break; } }
  }
  return {
    found: true,
    time: time,
    snippet: info[hit].text.replace(/\\n/g, ' ').trim().slice(0, 60),
    rows: info.length,
  };
}"""


def _classify(time_text: str) -> str:
    """把时间分隔符归类：today / not_today / unknown。"""
    t = (time_text or "").strip()
    if not t:
        return "unknown"
    if t.startswith("昨天") or t.startswith("前天") or t.startswith("星期"):
        return "not_today"
    if re.match(r"^\d{4}[/\-.]\d{1,2}", t):          # 2025/12/29
        return "not_today"
    if re.match(r"^\d{1,2}[/\-.]\d{1,2}$", t):       # 09/24
        return "not_today"
    if t == "刚刚" or t.endswith("前"):                # 刚刚 / N分钟前 / N小时前
        return "today"
    if re.match(r"^今天\s*\d{1,2}:\d{2}$", t) or re.match(r"^\d{1,2}:\d{2}$", t):
        return "today"
    return "unknown"


def _markers(task: TaskConfig) -> list[str]:
    """从配置里收集所有文字标记（用于在聊天里认出"我发的那条"）。"""
    out: list[str] = []

    def visit(msg: Message) -> None:
        if msg.type == "text" and msg.content:
            out.append(msg.content)
        for choice in msg.choices:
            visit(choice)

    for target in task.targets:
        for message in target.messages:
            visit(message)

    seen: set[str] = set()
    return [m for m in out if not (m in seen or seen.add(m))]


async def check_target(chat: DouyinChat, name: str, markers: list[str]) -> dict:
    """只读检查一个目标：今天发过没有。"""
    try:
        await chat.open_target(name, retries=0)
    except Exception as exc:
        return {"target": name, "status": "open_failed", "detail": str(exc), "time": "", "snippet": ""}

    try:
        data = await chat.page.evaluate(EXTRACT_JS, markers)
    except Exception as exc:
        return {"target": name, "status": "read_failed", "detail": str(exc), "time": "", "snippet": ""}

    if not isinstance(data, dict) or data.get("error"):
        return {
            "target": name,
            "status": "read_failed",
            "detail": str((data or {}).get("error", "未知读取错误")),
            "time": "",
            "snippet": "",
        }

    if not data.get("found"):
        return {"target": name, "status": "missing", "detail": "没有找到我发出的标记消息",
                "time": "", "snippet": ""}

    verdict = _classify(data.get("time", ""))
    return {
        "target": name,
        "status": "ok" if verdict == "today" else ("missing" if verdict == "not_today" else "uncertain"),
        "detail": verdict,
        "time": data.get("time", ""),
        "snippet": data.get("snippet", ""),
    }


async def repair_target(
    chat: DouyinChat,
    name: str,
    task: TaskConfig,
    history: History,
    run_date: str,
) -> dict:
    """给一个漏掉的目标补发配置里的全部消息，并写入防重复账本。"""
    target = next((t for t in task.targets if t.name == name), None)
    if target is None:
        return {"target": name, "sent": 0, "errors": ["配置里找不到该目标"]}

    sent = 0
    errors: list[str] = []
    try:
        await chat.open_target(name, retries=1)
    except Exception as exc:
        return {"target": name, "sent": 0, "errors": [f"打不开聊天: {exc}"]}

    for index, message in enumerate(target.messages):
        key = history.key(task.task_id, run_date, name, _message_id(index, message))
        # 先占位：与主任务同一套语义，发送结果不确定时宁可留着"unknown"也不重发
        history.reserve(key)
        try:
            await send_message(chat.page, chat, message, task.stickers)
        except Exception as exc:
            errors.append(f"第 {index + 1} 条: {exc}")
            continue
        history.mark_success(key)
        sent += 1
        if index < len(target.messages) - 1:
            await asyncio.sleep(random.uniform(task.interval_min, task.interval_max))

    return {"target": name, "sent": sent, "errors": errors}


def _print_report(results: list[dict], title: str = "复检结果") -> tuple[list[dict], list[dict]]:
    ok = [r for r in results if r["status"] == "ok"]
    bad = [r for r in results if r["status"] in {"missing", "open_failed", "read_failed"}]
    uncertain = [r for r in results if r["status"] == "uncertain"]

    print()
    print("=" * 62)
    print(f"  {title}：确认今天已发 {len(ok)} / {len(results)}")
    if bad:
        print(f"  ✗ 未确认 {len(bad)}:")
        for r in bad:
            extra = f" / {r['time']}" if r.get("time") else ""
            print(f"      - {r['target']}  ({r['detail']}{extra})")
    if uncertain:
        print(f"  ? 无法判断 {len(uncertain)}:")
        for r in uncertain:
            print(f"      - {r['target']}  时间={r['time']!r}")
    print("=" * 62)
    return ok, bad + uncertain


async def run(only: list[str] | None, out_json: Path | None, delay: float, fix: bool) -> int:
    settings = load_settings()
    task = load_task(settings)
    markers = _markers(task)
    if not markers:
        raise ConfigError("配置里没有文字消息，无法复检")

    targets = [t.name for t in task.targets]
    if only:
        wanted = set(only)
        targets = [n for n in targets if n in wanted]
        if not targets:
            raise ConfigError(f"--only 指定的目标都不在配置里：{only}")

    print(f"复检 {len(targets)} 个目标，标记文本: {markers}", flush=True)
    if fix:
        print("模式：复检 + 自动补发（会真实发送消息）", flush=True)
    print()

    history = History(settings.artifacts_dir / "history.json")
    run_date = history.run_date(task.timezone)

    results: list[dict] = []
    repairs: list[dict] = []
    async with open_douyin(settings) as session:
        page = session.page
        await open_private_messages(page)
        chat = DouyinChat(page, timeout_ms=int(task.target_open_timeout_seconds * 1000))

        # --- 第一轮：复检 ---
        for index, name in enumerate(targets, 1):
            result = await check_target(chat, name, markers)
            results.append(result)
            icon = {"ok": "✓", "missing": "✗", "uncertain": "?", "open_failed": "✗",
                    "read_failed": "✗"}[result["status"]]
            label = {"ok": "今天已发", "missing": "今天没发", "uncertain": "无法判断",
                     "open_failed": "打不开", "read_failed": "读不到"}[result["status"]]
            print(f"  [{index:>2}/{len(targets)}] {icon} {label:<6} 时间={result['time'] or '—':<12} {name}",
                  flush=True)
            if index < len(targets):
                await asyncio.sleep(delay)

        _, bad = _print_report(results)

        # --- 第二轮：补发 ---
        # 只对"确认没发"的补发；"无法判断"不动（可能是页面没渲染出来，重发有重复风险）
        to_fix = [r["target"] for r in results if r["status"] == "missing"]
        if fix and to_fix:
            print()
            print(f"开始补发 {len(to_fix)} 个目标：")
            for index, name in enumerate(to_fix, 1):
                outcome = await repair_target(chat, name, task, history, run_date)
                repairs.append(outcome)
                if outcome["errors"]:
                    print(f"  [{index}/{len(to_fix)}] ✗ {name}  成功 {outcome['sent']} 条，"
                          f"失败 {len(outcome['errors'])} 条: {outcome['errors']}", flush=True)
                else:
                    print(f"  [{index}/{len(to_fix)}] ✓ {name}  已补发 {outcome['sent']} 条", flush=True)
                if index < len(to_fix):
                    await asyncio.sleep(random.uniform(task.interval_min, task.interval_max))

            # --- 第三轮：重新复检补发过的 ---
            print()
            print("重新复检补发过的目标：")
            final: list[dict] = []
            for index, name in enumerate(to_fix, 1):
                result = await check_target(chat, name, markers)
                final.append(result)
                icon = "✓" if result["status"] == "ok" else "✗"
                print(f"  [{index}/{len(to_fix)}] {icon} {result['status']:<10} "
                      f"时间={result['time'] or '—':<12} {name}", flush=True)
                if index < len(to_fix):
                    await asyncio.sleep(delay)
            results = [r for r in results if r["target"] not in set(to_fix)] + final
            _, bad = _print_report(results, "最终结果")

    if out_json is not None:
        out_json.write_text(
            json.dumps({"markers": markers, "run_date": run_date,
                        "results": results, "repairs": repairs},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"详细结果: {out_json}")

    # 给调用方（daily_run.sh）解析用的一行汇总
    print(f"VERIFY_SUMMARY repaired={len(repairs)} missing={len(bad)} total={len(results)}")

    return 0 if not bad else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="复检今天是否每个会话都发过消息")
    parser.add_argument("--fix", action="store_true", help="复检后自动补发漏掉的目标（会真实发送）")
    parser.add_argument("--only", help="只看指定目标，逗号分隔")
    parser.add_argument("--json", type=Path, help="把详细结果写入 JSON")
    parser.add_argument("--delay", type=float, default=1.5, help="目标之间的间隔秒数，默认 1.5")
    args = parser.parse_args()

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    try:
        return asyncio.run(run(only, args.json, args.delay, args.fix))
    except (ConfigError, AuthenticationError, RiskControlError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

"""根据 chats.json 生成 config.json（任务配置）。

为什么需要这个脚本：好友名里可能有肉眼不可见的字符 —— 不间断空格 U+00A0、
零宽字符、emoji、变体选择符等（实测目标 `˶ᵒ ֊ ˂˶` 就含 U+00A0）。任务靠精确
匹配定位好友，手打这些名字必然失败。本脚本直接沿用 chats.json 里的原始字节。

用法：
    # 默认：选中 chats.json 中所有"带火花标识"的会话
    .venv/bin/python scripts/make_config.py

    # 指定名单（一行一个精确名称，UTF-8）
    .venv/bin/python scripts/make_config.py --names-file targets.txt

    # 自定义文案
    .venv/bin/python scripts/make_config.py --text "叮！自动续火花"

    # 覆盖输出（config.json 已被 .gitignore 忽略）
    .venv/bin/python scripts/make_config.py --out config.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TEXT = "叮！自动续火花"

# 与仓库自带的 config/stickers.json 一致。目前上游只提供这两个，
# 所以"随机表情"实际是在这两个之间随机。
STICKERS = {
    "比心": {"label": "比心", "category": "常用", "fallback_index": 3},
    "开心": {"label": "开心", "category": "常用", "fallback_index": 5},
}


def _load_chats(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"错误: 找不到 {path}\n"
            f"请先运行: .venv/bin/python scripts/list_chats.py --out chats.md"
        )
    if not isinstance(data, list):
        raise SystemExit(f"错误: {path} 格式异常")
    return data


def _pick_targets(chats: list[dict], names_file: Path | None) -> list[str]:
    by_name = {entry.get("name"): entry for entry in chats if entry.get("name")}

    if names_file is not None:
        raw = names_file.read_text(encoding="utf-8").splitlines()
        wanted = [line.rstrip("\n") for line in raw if line.strip()]
    else:
        wanted = [e["name"] for e in chats if e.get("streak")]

    missing = [name for name in wanted if name not in by_name]
    if missing:
        raise SystemExit(
            "错误: 以下名称在 chats.json 中找不到（请用 list_chats.py 重新导出）:\n  "
            + "\n  ".join(repr(name) for name in missing)
        )

    # 去重但保持顺序
    seen: set[str] = set()
    ordered: list[str] = []
    for name in wanted:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _describe(name: str, entry: dict) -> str:
    """给人类看的备注，写进配置旁边的清单里。"""
    kind = "群" if entry.get("is_group") else "好友"
    spark = entry.get("streak") or "—"
    return f"{kind} · 火花 {spark}"


def build_config(targets: list[str], chats: list[dict], text: str) -> dict:
    by_name = {entry.get("name"): entry for entry in chats if entry.get("name")}

    def target_block(name: str) -> dict:
        return {
            "name": name,
            # 文字 + 随机原生表情。注意上游 send_message() 每次只发一种类型，
            # 所以"文字+表情"是两条消息，不是一条。
            "messages": [
                {"type": "text", "content": text},
                {
                    "type": "random",
                    "choices": [
                        {"type": "douyin_sticker", "sticker": "比心"},
                        {"type": "douyin_sticker", "sticker": "开心"},
                    ],
                },
            ],
        }

    return {
        "_note": "由 scripts/make_config.py 生成；本文件已被 .gitignore 忽略，不要提交。",
        "task_id": "daily-streak",
        "timezone": "Asia/Shanghai",
        "targets": [target_block(name) for name in targets],
        "stickers": STICKERS,
        "send_interval_seconds": {"min": 3, "max": 8},
        # 一个目标失败不中断其余目标
        "continue_on_error": True,
        # 关键：四个补跑时间点靠它只补发失败的，已成功的自动跳过
        "prevent_duplicates": True,
        "target_open_retries": 1,
        "target_open_timeout_seconds": 15,
        "_targets_legend": {name: _describe(name, by_name.get(name, {})) for name in targets},
    }


def verify(config: dict) -> list[str]:
    """跑一遍仓库自己的配置校验，确保生成的东西真的能被任务解析。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from dataclasses import replace

    from app.config import load_settings, load_task

    out = PROJECT_ROOT / ".config-verify.json"
    out.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    try:
        settings = replace(load_settings(), task_config_path=out)
        task = load_task(settings)
        return [target.name for target in task.targets]
    finally:
        out.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 chats.json 生成 config.json")
    parser.add_argument("--chats", type=Path, default=PROJECT_ROOT / "chats.json")
    parser.add_argument("--names-file", type=Path, help="一行一个精确名称")
    parser.add_argument("--text", default=DEFAULT_TEXT, help=f"发送的文字，默认「{DEFAULT_TEXT}」")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "config.json")
    args = parser.parse_args()

    chats = _load_chats(args.chats)
    targets = _pick_targets(chats, args.names_file)

    if not targets:
        raise SystemExit("错误: 没有选中任何目标")

    config = build_config(targets, chats, args.text)

    # 先让仓库自己的解析器验证一遍，再落盘
    parsed = verify(config)
    if len(parsed) != len(targets):
        raise SystemExit(f"错误: 校验后目标数不一致（{len(parsed)} != {len(targets)}）")

    args.out.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"[✓] 已写入 {args.out}")
    print(f"[✓] 仓库配置解析器校验通过，共 {len(parsed)} 个目标")
    print()
    print(f"{'#':<3} {'类型':<5} {'火花':<14} 名称")
    print("-" * 66)
    by_name = {e.get("name"): e for e in chats if e.get("name")}
    for index, name in enumerate(parsed, 1):
        entry = by_name.get(name, {})
        kind = "群" if entry.get("is_group") else "好友"
        spark = entry.get("streak") or "—"
        print(f"{index:<3} {kind:<5} {spark:<14} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

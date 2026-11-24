"""昵称优先搜索 + 当日已互相对话则跳过。"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import load_task
from app.history import History
from app.main import _open_target_with_aliases
from app.models import Message, Settings, Target
from app.renewal import is_today_label, renewed_today


def _settings(path: Path) -> Settings:
    return Settings(
        task_config_path=path,
        storage_state="{}",
        cookie=None,
        headless=True,
        browser_path=None,
        artifacts_dir=path.parent / "artifacts",
        trace=False,
    )


def test_config_loads_nickname_and_skip_flag(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "旧备注",
                        "nickname": "新昵称",
                        "aliases": ["新昵称", "别的备注"],
                        "messages": [{"type": "text", "content": "hi"}],
                    }
                ],
                "skip_if_renewed_today": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    task = load_task(_settings(path))
    assert task.targets[0].nickname == "新昵称"
    assert task.targets[0].aliases == ("别的备注",)  # 与 nickname 相同的已去掉
    assert task.skip_if_renewed_today is False


@pytest.mark.asyncio
async def test_nickname_is_searched_first() -> None:
    chat = MagicMock()
    chat.open_target = AsyncMock()
    target = Target(name="旧备注", messages=(), nickname="新昵称", aliases=("别的备注",))

    matched = await _open_target_with_aliases(chat, target, 0)

    assert matched == "新昵称"
    assert [c.args[0] for c in chat.open_target.await_args_list] == ["新昵称"]


@pytest.mark.asyncio
async def test_falls_back_to_remark_when_nickname_not_found() -> None:
    chat = MagicMock()

    async def fake_open(name, retries):
        if name != "旧备注":
            raise RuntimeError("搜索不到目标好友")

    chat.open_target = AsyncMock(side_effect=fake_open)
    target = Target(name="旧备注", messages=(), nickname="新昵称", aliases=("别的备注",))

    matched = await _open_target_with_aliases(chat, target, 0)

    assert matched == "旧备注"
    assert [c.args[0] for c in chat.open_target.await_args_list] == [
        "新昵称",
        "旧备注",
    ]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("刚刚", True),
        ("5分钟前", True),
        ("2小时前", True),
        ("今天 15:30", True),
        ("15:30", True),
        ("昨天 20:51", False),
        ("前天", False),
        ("星期六", False),
        ("11/11", False),
        ("2026/11/11", False),
        ("", False),
    ],
)
def test_today_label(label: str, expected: bool) -> None:
    assert is_today_label(label) is expected


@pytest.mark.asyncio
async def test_renewed_today_requires_both_sides() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=[
            {"me": True, "time": "刚刚"},
            {"me": True, "time": ""},
        ]
    )
    assert await renewed_today(page) is False

    page.evaluate = AsyncMock(
        return_value=[
            {"me": True, "time": "刚刚"},
            {"me": False, "time": ""},  # 同组，时间向下传播
        ]
    )
    assert await renewed_today(page) is True


@pytest.mark.asyncio
async def test_renewed_today_ignores_yesterday() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=[
            {"me": True, "time": "昨天 20:51"},
            {"me": False, "time": ""},
        ]
    )
    assert await renewed_today(page) is False


def test_mark_skipped_counts_as_done(tmp_path: Path) -> None:
    history = History(tmp_path / "history.json")
    key = "daily-streak:2026-11-18:好友A:0-abc"
    history.mark_skipped(key)
    assert history.entries[key]["status"] == "success"
    assert history.entries[key]["reason"] == "already_renewed_today"


def test_verify_recognizes_skipped_target(tmp_path: Path) -> None:
    from app.main import _message_id
    from scripts.verify_sent import skipped_as_renewed

    history = History(tmp_path / "history.json")
    task = MagicMock()
    task.task_id = "daily-streak"
    message = Message(type="text", content="叮！自动续火花")
    target = Target(name="好友A", messages=(message,), nickname="新昵称")

    assert skipped_as_renewed(history, task, "2026-11-18", target) is False
    history.mark_skipped(
        history.key(task.task_id, "2026-11-18", target.name, _message_id(0, message))
    )
    assert skipped_as_renewed(history, task, "2026-11-18", target) is True

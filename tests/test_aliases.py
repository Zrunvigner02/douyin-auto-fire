"""改备注兜底：aliases 只影响打开聊天，不改变账本身份。"""

import json
from pathlib import Path

import pytest

from app.browser import AuthenticationError
from app.config import ConfigError, load_task
from app.douyin import PageOperationError
from app.main import _open_target_with_aliases
from app.models import Settings, Target
from unittest.mock import AsyncMock, MagicMock


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


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _target(aliases=()) -> Target:
    return Target(name="旧备注", messages=(), aliases=tuple(aliases))


def test_parse_aliases_from_list(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "targets": [
                {
                    "name": "旧备注",
                    "aliases": ["新备注", "旧备注", "新备注", " 另一个 "],
                    "messages": [{"type": "text", "content": "hi"}],
                }
            ]
        },
    )
    task = load_task(_settings(path))
    assert task.targets[0].name == "旧备注"  # 账本身份不变
    assert task.targets[0].aliases == ("新备注", "另一个")


def test_parse_alias_as_single_string(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "targets": [
                {
                    "name": "旧备注",
                    "aliases": "新备注",
                    "messages": [{"type": "text", "content": "hi"}],
                }
            ]
        },
    )
    assert load_task(_settings(path)).targets[0].aliases == ("新备注",)


def test_reject_invalid_aliases(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "targets": [
                {
                    "name": "旧备注",
                    "aliases": [123],
                    "messages": [{"type": "text", "content": "hi"}],
                }
            ]
        },
    )
    with pytest.raises(ConfigError, match="aliases"):
        load_task(_settings(path))


@pytest.mark.asyncio
async def test_primary_name_wins() -> None:
    chat = MagicMock()
    chat.open_target = AsyncMock()

    matched = await _open_target_with_aliases(chat, _target(["新备注"]), 1)

    assert matched == "旧备注"
    # 外层重试由 _open_target_with_retry 控制，单次调用固定 retries=0
    chat.open_target.assert_awaited_once_with("旧备注", retries=0)


@pytest.mark.asyncio
async def test_falls_back_to_alias_when_name_not_found() -> None:
    chat = MagicMock()

    async def fake_open(name, retries):
        if name != "新备注":
            raise PageOperationError("搜索不到目标好友")

    chat.open_target = AsyncMock(side_effect=fake_open)

    matched = await _open_target_with_aliases(chat, _target(["旧昵称", "新备注"]), 1)

    assert matched == "新备注"
    assert [call.args[0] for call in chat.open_target.await_args_list] == [
        "旧备注",
        "旧昵称",
        "新备注",
    ]


@pytest.mark.asyncio
async def test_auth_error_does_not_try_aliases() -> None:
    chat = MagicMock()
    chat.open_target = AsyncMock(side_effect=AuthenticationError("登录失效"))

    with pytest.raises(AuthenticationError):
        await _open_target_with_aliases(chat, _target(["新备注"]), 1)

    chat.open_target.assert_awaited_once()


@pytest.mark.asyncio
async def test_raises_last_error_when_all_aliases_fail() -> None:
    chat = MagicMock()
    chat.open_target = AsyncMock(side_effect=PageOperationError("搜索不到目标好友"))

    with pytest.raises(PageOperationError, match="搜索不到"):
        await _open_target_with_aliases(chat, _target(["新备注"]), 0)

    assert chat.open_target.await_count == 2

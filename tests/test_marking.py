"""「标记文字发出即成功」规则：表情包失败不判失败、不补发。"""

from pathlib import Path
from unittest.mock import MagicMock

from app.history import History
from app.marking import (
    REASON_STICKER_FAILED,
    is_marker_message,
    tolerate_attachment_failure,
)
from app.models import Message, Target
from scripts.verify_sent import marker_confirmed_in_ledger, skipped_as_renewed


def test_is_marker_message() -> None:
    assert is_marker_message(Message(type="text", content="叮！自动续火花")) is True
    assert is_marker_message(Message(type="text", content="")) is False
    assert is_marker_message(Message(type="douyin_sticker", sticker="比心")) is False
    assert is_marker_message(Message(type="image", path=Path("a.png"))) is False
    assert is_marker_message(Message(type="random", choices=(Message(type="text", content="x"),))) is False


def test_tolerate_attachment_failure() -> None:
    sticker = Message(type="douyin_sticker", sticker="比心")
    text = Message(type="text", content="叮！自动续火花")

    # 标记已发出 + 附加消息失败 → 容忍
    assert tolerate_attachment_failure(True, sticker) is True
    # 标记还没发出 → 不容忍，表情包失败仍是真失败
    assert tolerate_attachment_failure(False, sticker) is False
    # 标记自身失败 → 永不容忍
    assert tolerate_attachment_failure(True, text) is False
    assert tolerate_attachment_failure(False, text) is False


def test_mark_success_with_reason(tmp_path: Path) -> None:
    history = History(tmp_path / "history.json")
    key = "daily-streak:2026-11-19:好友A:1-abc"
    history.mark_success(key, reason=REASON_STICKER_FAILED)
    assert history.entries[key]["status"] == "success"
    assert history.entries[key]["reason"] == REASON_STICKER_FAILED


def test_ledger_confirms_marker_even_when_sticker_failed(tmp_path: Path) -> None:
    from app.main import _message_id

    history = History(tmp_path / "history.json")
    task = MagicMock()
    task.task_id = "daily-streak"
    marker = Message(type="text", content="叮！自动续火花")
    sticker = Message(type="douyin_sticker", sticker="比心")
    target = Target(name="好友A", messages=(marker, sticker))

    marker_key = history.key(
        task.task_id, "2026-11-19", target.name, _message_id(0, marker)
    )

    # 标记已发、表情包失败按规则记为 success → 账本确认标记已发
    history.mark_success(marker_key, reason=REASON_STICKER_FAILED)
    assert marker_confirmed_in_ledger(history, task, "2026-11-19", target) is True
    assert skipped_as_renewed(history, task, "2026-11-19", target) is False

    # unknown（结果不确定）不算确认
    history.reserve(marker_key)
    assert marker_confirmed_in_ledger(history, task, "2026-11-19", target) is False


def test_ledger_confirms_plain_success(tmp_path: Path) -> None:
    from app.main import _message_id

    history = History(tmp_path / "history.json")
    task = MagicMock()
    task.task_id = "daily-streak"
    marker = Message(type="text", content="叮！自动续火花")
    target = Target(name="好友A", messages=(marker,))

    history.mark_success(
        history.key(task.task_id, "2026-11-19", target.name, _message_id(0, marker))
    )
    assert marker_confirmed_in_ledger(history, task, "2026-11-19", target) is True

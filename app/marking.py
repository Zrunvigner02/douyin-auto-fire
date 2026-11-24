"""续火花的「标记发出即成功」判定。

一个目标的消息序列通常是：

    0. text   "叮！自动续火花"      ← 标记消息，决定火花是否续上
    1. random → douyin_sticker      ← 附加表情包，锦上添花

只要标记文字真正发出去了，火花就已经续上；表情包（或任何后续附加消息）
发送失败不影响结果，也不应该导致：目标判失败、当天反复重跑、复检误判为
"未发送成功"而补发。本模块把这条规则集中起来，供主任务和复检共用。
"""

from __future__ import annotations

from app.models import Message

# 写入 history.json 的 reason：标记已发出，但附加消息（表情包）失败后按规则视为成功。
REASON_STICKER_FAILED = "sticker_failed_mark_sent"


def is_marker_message(message: Message) -> bool:
    """是否是在聊天记录里能被认出的文字标记消息（如"叮！自动续火花"）。

    douyin_sticker / image / random 等附加消息都不是标记。
    """
    return message.type == "text" and bool(message.content)


def tolerate_attachment_failure(mark_sent: bool, message: Message) -> bool:
    """标记已发出、且当前失败的是附加消息时，是否容忍该失败。

    标记消息自身失败永远返回 False（必须当成真失败，交给上层处理）。
    """
    return mark_sent and not is_marker_message(message)

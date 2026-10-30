#!/usr/bin/env bash
#
# 一条命令看清最近状态 —— 只读，不启动浏览器、不发任何消息。
#
# 用法：./scripts/status.sh

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1
PYTHON="$PROJECT_ROOT/.venv/bin/python"
LABEL="com.zrun.douyin-fire"

echo "════════ 续火花 状态检查 ════════"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo

# --- 定时任务 ---------------------------------------------------------------
echo "【定时任务】"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null \
    | grep -Ei "^\s+(state|runs|last exit) " | sed 's/^/  /'
else
  echo "  ⚠️ 未安装（运行 ./scripts/install_launchd.sh）"
fi

# --- 凭证 -------------------------------------------------------------------
echo
echo "【登录凭证】"
if [ -f storage-state.json ]; then
  echo "  ✅ storage-state.json  修改于 $(stat -f '%Sm' storage-state.json)"
else
  echo "  ⚠️ 不存在，需要运行 scripts/login_auto.py"
fi

# --- 最近 7 天覆盖情况 --------------------------------------------------------
echo
echo "【最近 7 天】"
"$PYTHON" - <<'PY'
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

tz = ZoneInfo("Asia/Shanghai")
try:
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    expected = sum(len(t.get("messages") or []) for t in cfg["targets"])
    ntargets = len(cfg["targets"])
except Exception:
    print("  ⚠️ 读不到 config.json")
    raise SystemExit

try:
    hist = json.loads(Path("artifacts/history.json").read_text(encoding="utf-8"))
except Exception:
    hist = {}

today = datetime.now(tz).date()
bad_days = 0
for i in range(6, -1, -1):
    day = (today - timedelta(days=i)).isoformat()
    prefix = f":{day}:"
    n = sum(1 for k, v in hist.items()
            if prefix in k and isinstance(v, dict) and v.get("status") == "success")
    if n >= expected:
        mark, note = "✅", "完整"
        # 目标数改过之后，历史日期的记录数会多于当前期望值，这不是问题
        if n > expected:
            note = "完整（当时目标数更多）"
    elif n == 0:
        mark, note = "❌", "完全没发"
        bad_days += 1
    else:
        mark, note = "⚠️", "部分"
        bad_days += 1
    tagged = "  ← 今天" if i == 0 else ""
    print(f"  {day}  {mark} {n:>2} 条  {note}{tagged}")

print()
print(f"  目标 {ntargets} 个 × 每天 {expected} 条")
if bad_days:
    print(f"  ⚠️ 最近 7 天有 {bad_days} 天不完整")
else:
    print("  ✅ 最近 7 天全部完整")
PY

# --- 最近一次运行 ------------------------------------------------------------
echo
echo "【最近一次运行】"
grep -E "\[开始\]|\[完成\]|\[失败\]|\[跳过\]|\[复检\]|\[重试\]" \
  artifacts/logs/daily-run.log 2>/dev/null | tail -4 | sed 's/^/  /'

# --- 最近的错误 --------------------------------------------------------------
echo
echo "【日志里最近 3 类错误】"
grep -oE "(ERR_INTERNET_DISCONNECTED|搜索不到目标好友|登录状态已失效|要求进行安全验证|Timeout [0-9]+ms exceeded)" \
  artifacts/logs/daily-run.log 2>/dev/null \
  | sort | uniq -c | sort -rn | head -3 | sed 's/^/  /' || echo "  （无）"

# --- 运行中 ---------------------------------------------------------------
if [ -f artifacts/run.lock ]; then
  PID="$(cat artifacts/run.lock 2>/dev/null)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo
    echo "【正在运行】 PID $PID"
  fi
fi

echo
echo "════════════════════════════════"
echo "  详细日志: tail -f artifacts/logs/daily-run.log"
echo "  立即体检: .venv/bin/python scripts/verify_sent.py"

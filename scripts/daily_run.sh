#!/usr/bin/env bash
#
# 每日续火花包装脚本。
#
# 它负责四件事：
#   1. 启动浏览器之前先查"今天是否已完成"，是则直接退出（省掉 16 次多余的聊天窗口打开）
#   2. 上一次运行还在进行时不重复启动
#   3. 用 caffeinate 阻止空闲休眠，避免任务被反复挂起拖成几小时
#   4. 失败时发 macOS 通知，但只在"今天已无补跑机会"时才响铃
#
# 用法：
#   ./scripts/daily_run.sh                # 正常执行
#   ./scripts/daily_run.sh --dry-run      # 只验证登录和好友定位，不发送
#   ./scripts/daily_run.sh --force        # 忽略"今日已完成"检查，强制执行
#   ./scripts/daily_run.sh --no-notify    # 本次不发通知（调试用）

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

PYTHON="$PROJECT_ROOT/.venv/bin/python"
RESULT="$PROJECT_ROOT/artifacts/result.json"
LOCK="$PROJECT_ROOT/artifacts/run.lock"
LOG_DIR="$PROJECT_ROOT/artifacts/logs"
LOG="$LOG_DIR/daily-run.log"
STATE="$LOG_DIR/failure-notified"      # 记录今天是否已就失败发过通知
TZ_NAME="Asia/Shanghai"

# 与 launchd plist 里的 StartCalendarInterval 保持一致。
# 用途：判断"今天还有没有下一次补跑机会"。
SCHEDULE="00:10 09:30 15:00 20:00"

FORCE=0
NOTIFY=1
DRY=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --force)     FORCE=1 ;;
    --no-notify) NOTIFY=0 ;;
    --dry-run)   DRY=1; ARGS+=("$arg") ;;
    *)           ARGS+=("$arg") ;;
  esac
done

mkdir -p "$LOG_DIR"

# 日志轮转：上游的 artifacts/run.log 是追加模式且从不清理。
rotate() {
  local file="$1"
  [ -f "$file" ] || return 0
  local size
  size=$(stat -f%z "$file" 2>/dev/null || echo 0)
  if [ "$size" -gt 5242880 ]; then mv -f "$file" "$file.1"; fi
}

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$LOG"
}

# macOS 原生通知。失败不影响任务本身。
# 显式写出 osascript 的失败，避免"以为通知发出去了、其实被系统拦了"。
notify() {
  local title="$1" message="$2" sound="${3:-}"
  [ "$NOTIFY" -eq 1 ] || return 0
  local t m
  t=$(printf '%s' "$title"   | sed 's/\\/\\\\/g; s/"/\\"/g')
  m=$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')
  local script="display notification \"$m\" with title \"$t\""
  [ -n "$sound" ] && script="$script sound name \"$sound\""
  if ! osascript -e "$script" >/dev/null 2>&1; then
    log "[警告] macOS 通知发送失败（可能被隐私设置拦截），任务结果不受影响。"
  fi
}

# 今天还没到的时间点里，第一个是什么？没有则输出空串。
next_slot() {
  "$PYTHON" - "$SCHEDULE" "$TZ_NAME" <<'PY' 2>/dev/null
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

slots = [tuple(int(x) for x in s.split(":")) for s in sys.argv[1].split()]
now = datetime.now(ZoneInfo(sys.argv[2]))
for h, m in slots:
    if (h, m) > (now.hour, now.minute):
        print(f"{h:02d}:{m:02d}")
        break
PY
}

today() { date '+%Y-%m-%d'; }

if [ ! -x "$PYTHON" ]; then
  log "[错误] 找不到虚拟环境 Python: $PYTHON"
  notify "续火花：环境异常" "虚拟环境不存在：$PYTHON" "Basso"
  exit 2
fi

rotate "$LOG"
rotate "$PROJECT_ROOT/artifacts/run.log"

# --- 上一次运行是否仍在进行 ---------------------------------------------------
# 昨天的真实案例：00:18 那次因为反复休眠跑了 1 小时 36 分。若它还没结束就到了
# 下一个时间点，上游会抛 AlreadyRunningError（退出码 2），会被误报成"凭证失效"。
# 这里提前判断：锁存在且进程还活着 → 静默跳过，不算失败。
if [ -f "$LOCK" ]; then
  LOCK_PID="$(cat "$LOCK" 2>/dev/null || echo)"
  if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
    log "[跳过] 上一次运行（PID $LOCK_PID）仍在进行中，本次不启动。"
    exit 0
  fi
  log "[提示] 清理残留的运行锁（PID ${LOCK_PID:-未知} 已不存在）。"
  rm -f "$LOCK"
fi

# --- 今日是否已完成 -----------------------------------------------------------
# 以 history.json（防重复账本）为准，而不是 result.json。
#
# 为什么不用 result.json：它是每次运行都**覆盖**的输出文件，任何一次 `--dry-run`
# 都会把它冲掉（2026-10-08 就发生过），导致跳过失效、白白开一次浏览器。
# 而 history.json 只在**真实发送**时写入（上游 `if not dry_run:` 守卫），
# 是权威的"今天到底发了什么"记录 —— 它同时也是去重真正依赖的那份数据。
if [ "$FORCE" -eq 0 ]; then
  ALREADY_DONE="$("$PYTHON" - "$PROJECT_ROOT" "$TZ_NAME" <<'PY' 2>/dev/null
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(sys.argv[1])
tz = ZoneInfo(sys.argv[2])
today = datetime.now(tz).date().isoformat()

try:
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    task_id = cfg.get("task_id", "daily-streak")
    expected = sum(len(t.get("messages") or []) for t in cfg["targets"])
except Exception:
    print("no"); raise SystemExit

if expected <= 0:
    print("no"); raise SystemExit

try:
    hist = json.loads((root / "artifacts" / "history.json").read_text(encoding="utf-8"))
except Exception:
    print("no"); raise SystemExit

prefix = f"{task_id}:{today}:"
done = {
    key for key, value in hist.items()
    if key.startswith(prefix) and isinstance(value, dict) and value.get("status") == "success"
}
print("yes" if len(done) >= expected else "no")
PY
)"

  if [ "$ALREADY_DONE" = "yes" ]; then
    log "[跳过] 今日任务已全部成功，不启动浏览器。"
    # 今天早些时候失败过、现在已自动恢复 → 补一条通知，免得一直担心
    if [ -f "$STATE" ] && [ "$(cat "$STATE" 2>/dev/null)" = "$(today)" ]; then
      notify "续火花：已自动恢复" "今天 16 个目标已全部发送成功，无需处理。"
      rm -f "$STATE"
    fi
    exit 0
  fi
fi

# --- 执行 ---------------------------------------------------------------------
# caffeinate -i 阻止**空闲休眠**，让任务连续跑完（正常约 10 分钟）。
# 实测教训：2026-10-08 00:18 那次没加它，Mac 反复休眠，跑了 1 小时 36 分。
# 注意：合盖休眠（clamshell sleep）不受 caffeinate 影响，那种情况仍会被挂起。
RUNNER=()
if command -v caffeinate >/dev/null 2>&1; then RUNNER=(caffeinate -i); fi

do_run() {
  log "[开始] ${RUNNER[*]:-} python run.py ${ARGS[*]:-}"
  ${RUNNER[@]+"${RUNNER[@]}"} "$PYTHON" run.py ${ARGS[@]+"${ARGS[@]}"} 2>&1 | tee -a "$LOG"
  STATUS="${PIPESTATUS[0]}"
}

# 今天已成功写入账本的条数。用于判断"登录本来是好的、只是中途抖了一下"。
today_success_count() {
  "$PYTHON" - "$PROJECT_ROOT" "$TZ_NAME" <<'PY' 2>/dev/null
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(sys.argv[1]); tz = ZoneInfo(sys.argv[2])
try:
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    task_id = cfg.get("task_id", "daily-streak")
    hist = json.loads((root / "artifacts" / "history.json").read_text(encoding="utf-8"))
except Exception:
    print(0); raise SystemExit

prefix = f"{task_id}:{datetime.now(tz).date().isoformat()}:"
print(sum(1 for k, v in hist.items()
          if k.startswith(prefix) and isinstance(v, dict) and v.get("status") == "success"))
PY
}

do_run

# --- 瞬时抖动防护 -------------------------------------------------------------
# 2026-10-09 00:21 的真实故障：打开群聊时页面切换的瞬间，某个含"登录后"的元素
# 短暂可见，撞上只有 3 秒的登录复检窗口，被误判成凭证失效。而 AuthenticationError
# 会中断整个任务 —— 16 个目标只发了 8 个就停了。
#
# 判据：退出码 2（认证/配置类）但**今天已经有成功记录** —— 说明登录本来是好的，
# 是中途抖了一下。真正的凭证过期会在第一个目标就失败，不会有部分成功记录。
# 这种情况等 60 秒重跑一次；真是过期的话会再失败一次，代价只是多开一次浏览器。
if [ "$STATUS" -eq 2 ] && [ "$DRY" -eq 0 ]; then
  PARTIAL="$(today_success_count)"
  if [ -n "$PARTIAL" ] && [ "$PARTIAL" -gt 0 ]; then
    log "[重试] 本次失败，但今天已有 $PARTIAL 条成功发送记录（登录本来是好的），60 秒后重跑一次。"
    sleep 60
    do_run
    [ "$STATUS" -eq 0 ] && log "[重试] 已恢复。"
  fi
fi

# --- 结果与通知 ---------------------------------------------------------------
notified_today() { [ -f "$STATE" ] && [ "$(cat "$STATE" 2>/dev/null)" = "$(today)" ]; }

case "$STATUS" in
  0)
    log "[完成] 全部成功。"
    if notified_today; then
      notify "续火花：已自动恢复" "今天 16 个目标已全部发送成功，无需处理。"
      rm -f "$STATE"
    fi

    # --- 独立复检：回到每个聊天里核对今天到底发没发 -------------------------
    # 主任务报"成功"依据的是发送时的页面状态，那是**发送侧**的自我判断。
    # 这里做**接收侧**的独立核对，漏掉的当场补发。Dry Run 不做。
    if [ "$DRY" -eq 0 ]; then
      VERIFY_JSON="$LOG_DIR/verify-latest.json"
      log "[复检] 独立核对所有会话今天是否都发过消息…"
      "$PYTHON" scripts/verify_sent.py --fix --json "$VERIFY_JSON" 2>&1 | tee -a "$LOG"
      VRC="${PIPESTATUS[0]}"
      SUMMARY="$(grep -o 'VERIFY_SUMMARY.*' "$LOG" | tail -1)"
      REPAIRED="$(printf '%s' "$SUMMARY" | sed -n 's/.*repaired=\([0-9]*\).*/\1/p')"
      [ -n "$REPAIRED" ] || REPAIRED=0

      if [ "$VRC" -eq 0 ]; then
        if [ "$REPAIRED" -gt 0 ]; then
          log "[复检] 发现并补发了 $REPAIRED 个遗漏，最终全部确认。"
          notify "续火花：复检补发了遗漏" "复检发现 $REPAIRED 个会话今天没发，已自动补发并确认。"
        else
          log "[复检] 全部确认已发。"
        fi
      else
        log "[复检] 自动补发后仍有未确认的目标（退出码 $VRC）。详见 $VERIFY_JSON"
        notify "续火花：复检仍有遗漏" "自动补发后仍有会话未确认，请查看 artifacts/logs/verify-latest.json" "Basso"
      fi
    fi
    ;;

  1)
    # 部分目标失败。后续时间点会只补发失败的（prevent_duplicates 保证不重发）。
    NEXT="$(next_slot)"
    if [ -n "$NEXT" ]; then
      log "[完成] 部分目标失败；今天 $NEXT 会再试一次。"
      notify "续火花：部分目标失败" "本次有目标没发出去，今天 $NEXT 会自动重试（只补发失败的）。"
    else
      log "[失败] 部分目标失败，且今天已无补跑机会。"
      notify "续火花：今天可能没续上" "部分目标发送失败，今天已无自动重试。详见 artifacts/result.json" "Basso"
    fi
    [ -f "$STATE" ] || today > "$STATE"
    ;;

  2)
    # 配置 / 登录 / 风控问题 —— 后续时间点同样会失败，必须人工介入。
    NEXT="$(next_slot)"
    if [ -n "$NEXT" ]; then
      log "[失败] 配置或登录问题（如凭证失效、风控验证）。今天 $NEXT 仍会自动重试。"
      notify "续火花：需要处理" "登录凭证可能已失效或抖音要求验证。今天 $NEXT 会自动重试；若持续失败请重新扫码。"
    else
      log "[失败] 配置或登录问题，且今天已无补跑机会。需要人工处理。"
      notify "续火花：今天没续上" "登录凭证失效或需要验证，今天已无自动重试。请运行 scripts/login_auto.py 重新扫码。" "Basso"
    fi
    [ -f "$STATE" ] || today > "$STATE"
    ;;

  130)
    log "[取消] 任务被中断。"
    ;;

  *)
    NEXT="$(next_slot)"
    if [ -n "$NEXT" ]; then
      log "[失败] 退出码 $STATUS。今天 $NEXT 会再试一次。"
      notify "续火花：本次失败" "退出码 $STATUS，今天 $NEXT 会自动重试。"
    else
      log "[失败] 退出码 $STATUS，且今天已无补跑机会。"
      notify "续火花：今天没续上" "退出码 $STATUS，今天已无自动重试。详见 artifacts/logs/daily-run.log" "Basso"
    fi
    [ -f "$STATE" ] || today > "$STATE"
    ;;
esac

exit "$STATUS"

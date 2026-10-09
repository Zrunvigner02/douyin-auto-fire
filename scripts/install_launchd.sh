#!/usr/bin/env bash
#
# 安装 launchd 定时任务（macOS）。
#
# 会做三件事：
#   1. 前置检查：没有登录凭证或没有 config.json 就拒绝安装（避免装完立刻失败刷日志）
#   2. 把 deploy/launchd 的模板里的路径替换成本机实际项目路径
#   3. 拷贝到 ~/Library/LaunchAgents 并 bootstrap 加载
#
# 用法：
#   ./scripts/install_launchd.sh            # 正常安装
#   ./scripts/install_launchd.sh --force    # 跳过前置检查
#
# 注意：plist 里 RunAtLoad=true，所以安装完成的那一刻就会触发一次检查。
# 如果今天还没跑过，它会立刻开始执行任务。

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.zrun.douyin-fire"
TEMPLATE="$PROJECT_ROOT/deploy/launchd/$LABEL.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/$LABEL.plist"
UID_NUM="$(id -u)"

FORCE=0
for arg in "$@"; do
  [ "$arg" = "--force" ] && FORCE=1
done

fail() { printf '\033[31m错误:\033[0m %s\n' "$1" >&2; exit 1; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
info() { printf '  %s\n' "$1"; }

[ -f "$TEMPLATE" ] || fail "找不到 plist 模板: $TEMPLATE"

# --- 前置检查 ---------------------------------------------------------------
if [ "$FORCE" -eq 0 ]; then
  MISSING=0
  if [ ! -f "$PROJECT_ROOT/storage-state.json" ] && [ ! -f "$PROJECT_ROOT/cookie.json" ]; then
    printf '\033[33m!\033[0m 缺少登录凭证（storage-state.json）。请先运行:\n'
    info ".venv/bin/python scripts/login.py"
    MISSING=1
  fi
  if [ ! -f "$PROJECT_ROOT/config.json" ]; then
    printf '\033[33m!\033[0m 缺少 config.json（任务配置）。\n'
    MISSING=1
  fi
  if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    printf '\033[33m!\033[0m 虚拟环境不存在: $PROJECT_ROOT/.venv\n'
    MISSING=1
  fi
  [ "$MISSING" -eq 1 ] && fail "前置条件未满足，已中止安装。确认要强行安装请加 --force"
fi

mkdir -p "$TARGET_DIR" "$PROJECT_ROOT/artifacts/logs"

# --- 用本机实际路径渲染模板 --------------------------------------------------
# 模板里写死的 /Users/zrun/Desktop/douyin 替换成当前项目根目录，
# 这样项目被移动或换机器后，重跑本脚本即可修正。
sed "s|/Users/zrun/Desktop/douyin|$PROJECT_ROOT|g" "$TEMPLATE" > "$TARGET"

# 断言替换生效，避免 silently 装了个指向旧路径的任务
if grep -q "/Users/zrun/Desktop/douyin" "$TARGET" && [ "$PROJECT_ROOT" != "/Users/zrun/Desktop/douyin" ]; then
  fail "路径替换失败，请检查模板"
fi

plutil -lint "$TARGET" >/dev/null 2>&1 || fail "生成的 plist 不是合法 XML"

# --- 加载（先卸载旧的，保证幂等） ---------------------------------------------
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1
# 也尝试旧式 unload，兼容部分系统
launchctl unload "$TARGET" >/dev/null 2>&1

if ! launchctl bootstrap "gui/$UID_NUM" "$TARGET" 2>/dev/null; then
  # 回退到旧式 load
  launchctl load -w "$TARGET" 2>/dev/null || fail "launchctl 加载失败，请手动检查: $TARGET"
fi

ok "已安装并加载: $TARGET"
echo
echo "已配置的触发时间（每天）：00:10 / 09:30 / 15:00 / 20:00，以及每次登录时检查一次"
echo
echo "查看状态:   launchctl print gui/$UID_NUM/$LABEL | head -40"
echo "查看日志:   tail -f $PROJECT_ROOT/artifacts/logs/daily-run.log"
echo "手动跑一次: $PROJECT_ROOT/scripts/daily_run.sh"
echo "卸载:       $PROJECT_ROOT/scripts/uninstall_launchd.sh"

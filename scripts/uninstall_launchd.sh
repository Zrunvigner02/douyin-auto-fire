#!/usr/bin/env bash
#
# 卸载 launchd 定时任务。
#
# 只移除定时器，不删除项目文件、不删除登录凭证、不删除发送历史。

set -uo pipefail

LABEL="com.zrun.douyin-fire"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1
launchctl unload "$TARGET" >/dev/null 2>&1

if [ -f "$TARGET" ]; then
  rm -f "$TARGET"
  printf '\033[32m✓\033[0m 已卸载并删除: %s\n' "$TARGET"
else
  printf '\033[33m!\033[0m 未找到 %s（可能本来就未安装）\n' "$TARGET"
fi

echo
echo "项目文件、登录凭证、发送历史均未改动。"
echo "如残留运行锁，可手动删除: artifacts/run.lock"

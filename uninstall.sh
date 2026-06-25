#!/bin/bash
# FlClash Probe 卸载
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/flclash-probe"
SWIFTBAR_PLUGIN="$HOME/Library/Application Support/SwiftBar/Plugins/flclash-probe.5m.sh"
PLIST="$HOME/Library/LaunchAgents/com.user.flclash-probe.plist"

echo "==> 卸载 FlClash Probe"

launchctl bootout "gui/$(id -u)/com.user.flclash-probe" 2>/dev/null || true
rm -f "$PLIST"
rm -f "$SWIFTBAR_PLUGIN"

read -r -p "是否删除数据目录 $APP_DIR ? [y/N] " ans
if [[ "${ans,,}" == "y" ]]; then
  rm -rf "$APP_DIR"
  echo "    已删除 $APP_DIR"
else
  echo "    保留 $APP_DIR（程序文件仍在，仅移除定时任务和菜单）"
fi

echo "✅ 卸载完成"

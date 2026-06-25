#!/bin/bash
# FlClash Probe 一键安装 (macOS)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Library/Application Support/flclash-probe"
SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
PLIST_SRC="$SCRIPT_DIR/launchd/com.user.flclash-probe.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.user.flclash-probe.plist"
MIHOMO_BIN="$APP_DIR/bin/mihomo"

echo "==> FlClash Probe 安装"
echo "    目标目录: $APP_DIR"

# 1. Homebrew
if ! command -v brew &>/dev/null; then
  echo "==> 安装 Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
fi

# 2. SwiftBar
if ! brew list --cask swiftbar &>/dev/null 2>&1; then
  echo "==> 安装 SwiftBar..."
  brew install --cask swiftbar
fi

# 3. 创建目录并复制文件
mkdir -p "$APP_DIR/bin" "$SWIFTBAR_DIR" "$HOME/Library/LaunchAgents"
echo "==> 复制程序文件..."
cp "$SCRIPT_DIR/probe.py" "$APP_DIR/"
cp "$SCRIPT_DIR/run-probe.sh" "$APP_DIR/"
chmod +x "$APP_DIR/probe.py" "$APP_DIR/run-probe.sh"

if [[ ! -f "$APP_DIR/config.json" ]]; then
  cp "$SCRIPT_DIR/config.json.example" "$APP_DIR/config.json"
  echo "    已创建 config.json（请按需修改 profile_id）"
fi
if [[ ! -f "$APP_DIR/services.json" ]]; then
  cp "$SCRIPT_DIR/services.json.example" "$APP_DIR/services.json"
  echo "    已创建 services.json"
fi

# 4. Python venv
if [[ ! -d "$APP_DIR/venv" ]]; then
  echo "==> 创建 Python 虚拟环境..."
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install -q --upgrade pip pyyaml

# 5. mihomo 内核
if [[ ! -x "$MIHOMO_BIN" ]]; then
  echo "==> 下载 mihomo 内核..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    arm64) MIHOMO_ARCH="darwin-arm64" ;;
    x86_64) MIHOMO_ARCH="darwin-amd64" ;;
    *) echo "不支持的架构: $ARCH"; exit 1 ;;
  esac
  TMP="$(mktemp -d)"
  URL="https://github.com/MetaCubeX/mihomo/releases/latest/download/mihomo-${MIHOMO_ARCH}.gz"
  curl -fsSL "$URL" -o "$TMP/mihomo.gz"
  gunzip -c "$TMP/mihomo.gz" > "$MIHOMO_BIN"
  chmod +x "$MIHOMO_BIN"
  rm -rf "$TMP"
  echo "    mihomo 已安装: $($MIHOMO_BIN -v 2>&1 | head -1)"
fi

# 6. SwiftBar 插件
cp "$SCRIPT_DIR/swiftbar/flclash-probe.5m.sh" "$SWIFTBAR_DIR/"
chmod +x "$SWIFTBAR_DIR/flclash-probe.5m.sh"
echo "==> SwiftBar 插件已安装"

# 7. launchd 定时任务
sed "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
launchctl bootout "gui/$(id -u)/com.user.flclash-probe" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "==> launchd 已注册（每 20 分钟自动检测）"

# 8. 首次探测
echo "==> 启动首次探测（后台运行，可在 SwiftBar 菜单查看进度）..."
launchctl kickstart -k "gui/$(id -u)/com.user.flclash-probe" 2>/dev/null \
  || "$APP_DIR/run-probe.sh" &

echo ""
echo "✅ 安装完成！"
echo ""
echo "下一步："
echo "  1. 打开 SwiftBar（菜单栏会出现 🔍 图标）"
echo "  2. 点击图标可切换检测商、管理测速服务、立即重测"
echo "  3. 配置文件: $APP_DIR/config.json"
echo "  4. 测速名单: $APP_DIR/services.json"
echo "  5. 日志: $APP_DIR/probe.log"

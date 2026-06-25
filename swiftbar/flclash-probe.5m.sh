#!/bin/bash
# SwiftBar plugin: FlClash proxy probe status
# <xbar.title>FlClash Probe</xbar.title>
# <xbar.version>v2.0</xbar.version>
# <xbar.author>flclash-probe</xbar.author>

DIR="$HOME/Library/Application Support/flclash-probe"
STATE="$DIR/state.json"
CONFIG="$DIR/config.json"
SERVICES="$DIR/services.json"
LOG="$DIR/probe.log"
SELF="$0"

py() {
  "$DIR/venv/bin/python3" - "$@" 2>/dev/null || python3 - "$@"
}

set_provider() {
  py <<PY
import json
from pathlib import Path
cfg_path = Path("$CONFIG")
cfg = {"provider": "ip-api"}
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        pass
cfg["provider"] = "$1"
cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

toggle_service() {
  py <<PY
import json
from pathlib import Path
path = Path("$SERVICES")
default = {"services": []}
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = default
else:
    data = default
services = data.get("services") or data if isinstance(data, list) else []
if isinstance(data, dict):
    services = data.get("services", [])
idx = int("$1")
if 0 <= idx < len(services):
    services[idx]["enabled"] = not services[idx].get("enabled", True)
    path.write_text(json.dumps({"services": services}, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

add_service() {
  local name="$1" url="$2" weight="${3:-1.0}"
  py <<PY
import json
from pathlib import Path
path = Path("$SERVICES")
services = []
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        services = data.get("services", data if isinstance(data, list) else [])
    except json.JSONDecodeError:
        pass
services.append({"name": """$name""", "url": """$url""", "weight": float("""$weight"""), "enabled": True})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"services": services}, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

kick_probe() {
  launchctl kickstart -k "gui/$(id -u)/com.user.flclash-probe" 2>/dev/null \
    || launchctl start com.user.flclash-probe 2>/dev/null \
    || "$DIR/run-probe.sh" &
}

case "${1:-}" in
  set-provider)
    set_provider "${2:-ip-api}"
    exit 0
    ;;
  toggle-service)
    toggle_service "${2:-0}"
    exit 0
    ;;
  add-service)
    add_service "${2:-Custom}" "${3:-https://www.google.com/generate_204}" "${4:-1.0}"
    exit 0
    ;;
  probe-now)
    kick_probe
    exit 0
    ;;
  open-report)
    "$DIR/venv/bin/python3" "$DIR/probe.py" report 2>/dev/null \
      || python3 "$DIR/probe.py" report
    exit 0
    ;;
  prompt-add)
    RESULT=$(osascript <<'APPLESCRIPT' 2>/dev/null || true
set dlg to display dialog "添加测速服务" default answer "https://www.google.com/generate_204" buttons {"取消", "添加"} default button "添加" with title "FlClash Probe"
if button returned of dlg is "添加" then
  set u to text returned of dlg
  set ndlg to display dialog "服务名称" default answer "Custom" buttons {"取消", "确定"} default button "确定"
  return (text returned of ndlg) & "|||" & u
end if
APPLESCRIPT
)
    if [[ -n "$RESULT" && "$RESULT" == *"|||"* ]]; then
      NAME="${RESULT%%|||*}"
      URL="${RESULT#*|||}"
      add_service "$NAME" "$URL" "1.0"
    fi
    exit 0
    ;;
esac

if [[ ! -f "$STATE" ]]; then
  echo "🔍 --"
  echo "---"
  echo "等待首次检测... | bash=$SELF param1=probe-now terminal=false refresh=true"
  exit 0
fi

py <<'PY'
import json, os, shlex
from datetime import datetime
from pathlib import Path

dir_path = Path(os.path.expanduser("~/Library/Application Support/flclash-probe"))
state_path = dir_path / "state.json"
config_path = dir_path / "config.json"
services_path = dir_path / "services.json"
self_script = os.path.expanduser("~/Library/Application Support/SwiftBar/Plugins/flclash-probe.5m.sh")

state = json.loads(state_path.read_text(encoding="utf-8"))
cfg = {"provider": "ip-api"}
if config_path.exists():
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        pass

services = []
if services_path.exists():
    try:
        data = json.loads(services_path.read_text(encoding="utf-8"))
        services = data.get("services", data if isinstance(data, list) else [])
    except json.JSONDecodeError:
        pass
enabled_count = sum(1 for s in services if s.get("enabled", True))

providers = {
    "ip-api": "ip-api.com",
    "ipwho": "ipwho.is",
    "ipinfo": "ipinfo.io",
}
current = cfg.get("provider", "ip-api")
provider_label = providers.get(current, current)

status = state.get("status", "?")
progress = state.get("progress", "")
last = state.get("last_run", "")
nodes = state.get("nodes") or {}

icon = {"running": "⏳", "done": "✅", "error": "❌", "idle": "🔍"}.get(status, "🔍")
ok = sum(1 for n in nodes.values() if n.get("ok"))
total = len(nodes)
title = f"{icon} {ok}/{total}" if total else f"{icon} --"
if status == "running":
    title = f"⏳ {progress}"

print(title)
print("---")
if last:
    try:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        print(f"上次: {dt.strftime('%m-%d %H:%M')}")
    except Exception:
        print(f"上次: {last[:16]}")
print(f"状态: {status} {progress}")
print(f"检测商: {provider_label} | 测速服务: {enabled_count} 项")
print("---")
print("节点 | IP | F | S | 延时 | 类型 (F↑ S↓ 延时↑)")
sorted_nodes = sorted(
    nodes.items(),
    key=lambda item: (
        2 if not item[1].get("ok", True) else 0,
        int(item[1].get("fraud", 99)),
        -int(item[1].get("service_score", 0)),
        float(item[1].get("service_avg_ms") or 999999),
        item[0],
    ),
)
for name, info in sorted_nodes[:12]:
    base = info.get("base", "?")
    flag = info.get("flag", "")
    label = f"{flag} {base}".strip() if flag else base
    ip = info.get("ip", "?")
    f = info.get("fraud", "?")
    s = info.get("service_score", 0)
    avg = info.get("service_avg_ms")
    avg_disp = f"{int(avg)}ms" if avg is not None else "—"
    t = info.get("type", "?")
    print(f"{label} | {ip} | F{f} | S{s} | {avg_disp} | {t}")
    svc_bits = []
    for svc in info.get("services") or []:
        mark = "✓" if svc.get("ok") else "✗"
        ms = svc.get("ms", "?")
        svc_bits.append(f"{mark}{svc.get('name','?')}:{ms}ms")
    if svc_bits:
        print(f"   {' '.join(svc_bits)} | disabled=true")
if len(nodes) > 12:
    print(f"... 共 {len(nodes)} 节点")
print("---")
print(f"--检测商 (当前: {provider_label})")
for pid, label in providers.items():
    checked = " checked=true" if pid == current else ""
    cmd = f"bash={shlex.quote(self_script)} param1=set-provider param2={pid} terminal=false refresh=true"
    print(f"{label}{checked} | {cmd}")
print("---")
print(f"--测速服务 ({enabled_count} 项启用)")
for i, svc in enumerate(services[:10]):
    en = svc.get("enabled", True)
    mark = "✓" if en else "✗"
    w = svc.get("weight", 1)
    name = svc.get("name", "?")
    url = svc.get("url", "")
    short_url = url[:40] + ("…" if len(url) > 40 else "")
    toggle = f"bash={shlex.quote(self_script)} param1=toggle-service param2={i} terminal=false refresh=true"
    print(f"{mark} {name} (w={w}) | {toggle}")
    print(f"   {short_url} | disabled=true")
if len(services) > 10:
    print(f"... 共 {len(services)} 项 | disabled=true")
add_cmd = f"bash={shlex.quote(self_script)} param1=prompt-add terminal=false refresh=true"
print(f"➕ 添加服务... | {add_cmd}")
edit_cmd = f"bash=/usr/bin/open param1={shlex.quote(str(services_path))} terminal=false"
print(f"📝 编辑 services.json | {edit_cmd}")
print("---")
kick = f"bash={shlex.quote(self_script)} param1=probe-now terminal=false refresh=true"
print(f"🔄 立即重测 | {kick}")
report_cmd = f"bash={shlex.quote(self_script)} param1=open-report terminal=false"
print(f"📊 打开调试报告 (HTML) | {report_cmd}")
report_path = dir_path / "report.html"
open_report = f"bash=/usr/bin/open param1={shlex.quote(str(report_path))} terminal=false"
print(f"📄 打开 report.html | {open_report}")
json_path = dir_path / "report.json"
open_json = f"bash=/usr/bin/open param1={shlex.quote(str(json_path))} terminal=false"
print(f"📋 打开 report.json | {open_json}")
print(f"打开日志 | bash=/bin/open param1=-a param2=Console param3='{dir_path}/probe.log' terminal=false")
PY

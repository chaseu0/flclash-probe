# FlClash Probe

Mac 专用 FlClash 代理出口检测工具：自动探测每个节点的出口 IP、欺诈分、VPN/类型，并对 Google/YouTube 等国外服务做真实连通性测速，写回 Clash 配置并排序。

## 功能

- **出口 IP 检测**：经每个代理单独启动 mihomo，查询真实出口 IP
- **欺诈分 (F)**：基于 ip-api / ipwho / ipinfo 判断代理/VPN/机房/移动网络
- **服务测速 (S)**：对 Google、YouTube、Facebook、Netflix、ChatGPT、TikTok 等做 HEAD/GET 测速
- **智能命名**：保留国旗 emoji + 完整 IP + 各项分数
- **自动排序**：写回 `proxy-groups`，按 F 升序 → S 降序 → 失败排最后
- **SwiftBar 菜单**：切换检测商、管理测速名单、立即重测
- **定时任务**：每 20 分钟自动跑一轮（launchd）

## 节点命名格式

```
🇭🇰 HK01|103.197.71.19|F8|·|R|S82|420ms
│   │    │              │  │ │ │      │
│   │    │              │  │ │ │      └─ 加权平均延时 (如 420ms 或 3.2s)
│   │    │              │  │ │ └──────── 服务测速分 (0-99)
│   │    │              │  │ └─── 类型: R=住宅 M=移动 H=机房
│   │    │              │  └───── V=VPN ·=非VPN
│   │    │              └──────── 欺诈分 (越低越好)
│   │    └─────────────────────── 完整出口 IP
│   └──────────────────────────── 节点代号
└──────────────────────────────── 国旗 (有则保留，无则按地区码推断)
```

## 排序逻辑

Clash 没有按自定义字段排序，本工具在写回 YAML 时对 `proxy-groups` 的 `proxies` 列表排序：

1. **前缀节点不动**：`自动选择`、`故障转移`、`DIRECT`、流量信息等保持最前
2. **欺诈分 F 升序**：F8 排在 F36 前面（越低越像真实住宅）
3. **服务分 S 降序**：F 相同时，S82 排在 S40 前面
4. **平均延时升序**：S 相同时，420ms 排在 800ms 前面（越快越好）
5. **失败排最后**：IP 获取失败或任一测速服务不可达 → S0，排到最后

## 服务测速说明

与 Clash 自带的 `url-test` 延迟不同，本工具经**代理出口**访问真实国外服务：

| 服务 | 默认 URL | 权重 |
|------|----------|------|
| Google | `https://www.google.com/generate_204` | 1.0 |
| YouTube | `https://www.youtube.com/generate_204` | 1.0 |
| Facebook | `https://www.facebook.com/robots.txt` | 1.0 |
| Netflix | `https://www.netflix.com/generate_204` | 1.0 |
| ChatGPT | `https://chatgpt.com/` | 1.0 |
| TikTok | `https://www.tiktok.com/robots.txt` | 1.0 |

- 每项可单独启用/禁用，可自定义 URL 和权重
- **任一项超时或不可达 → 该节点 S=0**（严格惩罚）
- 全部通过时，按加权平均延迟换算为 0-99 分（越快越高）
- 超时默认 8 秒；节点内 6 项并行，节点间串行

配置文件：`~/Library/Application Support/flclash-probe/services.json`

## 一键安装

```bash
cd flclash-probe
chmod +x install.sh uninstall.sh
./install.sh
```

安装脚本会自动：

1. 检查/安装 Homebrew、SwiftBar
2. 复制文件到 `~/Library/Application Support/flclash-probe/`
3. 创建 Python 虚拟环境，安装 PyYAML
4. 下载 mihomo 内核（若无）
5. 安装 SwiftBar 菜单插件
6. 注册 launchd 定时任务（每 20 分钟）
7. 后台启动首次探测

## 手动配置

编辑 `~/Library/Application Support/flclash-probe/config.json`：

```json
{
  "provider": "ip-api",
  "ipinfo_token": "",
  "profile_id": "你的FlClash配置ID"
}
```

- `provider`：`ip-api`（默认）、`ipwho`、`ipinfo`
- `profile_id`：FlClash 配置目录下 `profiles/` 中的 yaml 文件名（不含扩展名）

## SwiftBar 菜单操作

菜单栏点击 **🔍** 图标：

| 操作 | 说明 |
|------|------|
| **检测商** | 切换 ip-api.com / ipwho.is / ipinfo.io |
| **测速服务** | 点击 ✓/✗ 启用或禁用某项 |
| **➕ 添加服务** | 弹窗输入名称和 URL |
| **📝 编辑 services.json** | 用系统默认编辑器批量修改 |
| **🔄 立即重测** | 立刻跑一轮完整探测 |
| **📊 打开调试报告** | 生成并打开 HTML 调试面板（各网站测速、欺诈分来源、API 原始返回） |
| **📄 打开 report.html** | 直接打开上次生成的报告 |
| **📋 打开 report.json** | 查看原始 JSON 数据 |
| **打开日志** | 查看 probe.log |

## 调试报告

每次探测结束会自动生成：

- `~/Library/Application Support/flclash-probe/report.html` — 可视化调试面板
- `~/Library/Application Support/flclash-probe/report.json` — 完整原始数据

也可命令行生成并打开：

```bash
~/Library/Application\ Support/flclash-probe/venv/bin/python3 \
  ~/Library/Application\ Support/flclash-probe/probe.py report
```

报告内容包括：每个节点对各网站的测速（ms、成功/超时）、欺诈分 API 来源与字段、F 分计算公式与分解、API 原始 JSON 返回。

## 卸载

```bash
./uninstall.sh
```

## 目录结构

```
flclash-probe/
├── README.md
├── install.sh
├── uninstall.sh
├── probe.py              # 主程序
├── run-probe.sh
├── config.json.example
├── services.json.example
├── swiftbar/
│   └── flclash-probe.5m.sh
└── launchd/
    └── com.user.flclash-probe.plist
```

## 依赖

- macOS 12+
- Python 3（系统自带即可）
- Homebrew（安装脚本自动处理）
- FlClash 已安装并有有效订阅配置
- FlClash 外部控制器：`127.0.0.1:9090`（安装时自动写入）

## 故障排查

| 问题 | 处理 |
|------|------|
| 菜单栏没图标 | 打开 SwiftBar 应用，确认插件目录有 `flclash-probe.5m.sh` |
| 全部 S0 | 检查代理是否真的能访问外网；可在 services.json 临时禁用难测的项 |
| IP 显示 ? | 切换检测商试试；ip-api 免费版有频率限制 |
| 配置没更新 | 看 probe.log；确认 FlClash 在运行 |

## 许可

MIT

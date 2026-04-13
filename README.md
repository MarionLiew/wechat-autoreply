# 企业微信自动回复（Mac 桌面端方案）

基于 macOS 辅助功能 API，直接监听企业微信客户端未读消息并自动回复。

## 安装（新机器首次使用）

1. clone 本仓库
   ```bash
   git clone git@github.com:MarionLiew/wechat-autoreply.git
   cd wechat-autoreply
   ```
2. Finder 双击 `setup.command`，等脚本自动安装 Python/依赖
3. 系统设置 → 隐私与安全性 → **辅助功能** → 勾选 Terminal（或运行 Python 的终端）
4. 管理界面会自动弹出，按向导填 LLM API Key、规则等

## 日常启动

双击 `start.command`，浏览器自动打开管理界面（`http://localhost:8501`）。
在管理界面里启停守护进程、改规则、看消息日志、改高级设置。

## 调试日志

- `daemon.log` — 守护进程日志
- `debug-setup.log` / `debug-start.log` — 安装/启动脚本日志

## 核心文件

| 文件 | 作用 |
|---|---|
| `run.py` | 守护进程入口（轮询企微 Mac 客户端） |
| `admin/app.py` | Streamlit 管理界面 |
| `wecom/mac_watcher.py` | AX 读写企业微信 |
| `reply/engine.py` | 规则 → 废话库 → LLM 三级回复 |
| `rules.json` / `fillers.json` | 规则库 / 废话库 |

## 已知限制

AX 层面**无法区分**聊天面板里某条消息是"我方"还是"对方"发的（都是 `AXStaticText`）。
本项目用两层防护缓解自回环：
1. 维护每个 sender 最近 `echo_protect_seconds`（默认 120s）内 bot 发出的回复，
   计数式地从 AX 读到的消息中扣除；
2. 若检测到 `tick` 的随机延迟期间客户又发了新消息，本轮放弃回复，下轮合并处理。

**仍然可能出错的场景**：
- **"经营大厅 / 快捷回复 / 人工客服"等功能面板作为浮层打开时**：会挡住聊天 AXWebArea，
  导致 bot 只能用会话行预览（最后一条消息）处理，无法读取完整多条上下文。
  日志会出现 `WARNING WebArea 黑名单` 提示。建议运行 bot 时保持聊天界面为活动视图，
  不打开功能浮层。
- **人工用同一企微账号手动回复**：不在 `_recent_replies_by_sender` 记录里，
  下轮 bot 会把人工消息当客户消息再次自动回复。建议人工介入时先在高级设置停掉 daemon。
- **消息发送半途失败**（写入成功但回车失败）：下轮可能重复。概率极低。

这两项可在未来通过 `AXPosition.x`（左右对齐）判断发送方来彻底解决，但需要更深的
AX 结构解析。

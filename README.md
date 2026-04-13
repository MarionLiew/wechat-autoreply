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

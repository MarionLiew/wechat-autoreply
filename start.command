#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 企业微信自动回复 — 日常启动
# 双击此文件即可运行（macOS Finder）
# ──────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

# ── 调试日志：所有输出同时写入 debug-start.log ────────────────
LOG_FILE="debug-start.log"
exec > >(tee "$LOG_FILE") 2>&1
echo "===== start.command 运行于 $(date '+%Y-%m-%d %H:%M:%S') ====="

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }

echo -e "\n${CYAN}${BOLD}╔══════════════════════════════════════╗"
echo -e      "║   企业微信自动回复 — 启动中…          ║"
echo -e      "╚══════════════════════════════════════╝${NC}\n"

# ── 环境自检 ──────────────────────────────────────────────────
echo "工作目录：$(pwd)"
echo "macOS：$(sw_vers -productVersion 2>/dev/null || echo unknown) / $(uname -m)"
echo "当前时间：$(date '+%Y-%m-%d %H:%M:%S')"

if [[ ! -d ".venv" ]]; then
    echo -e "${RED}✗ 未找到虚拟环境，请先双击运行 setup.command 完成安装。${NC}"
    echo -e "${YELLOW}调试日志：$(pwd)/$LOG_FILE${NC}"
    echo
    read -p "按回车键关闭…"
    exit 1
fi

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
source .venv/bin/activate
ok "Python：$(python --version) @ $(which python)"

# 关键文件检查
for f in config.py rules.json admin/app.py; do
    [[ -e "$f" ]] && ok "存在：$f" || warn "缺失：$f"
done

# .env 检查（不打印内容，只看是否存在及关键字段）
if [[ -f ".env" ]]; then
    ok ".env 存在"
    for k in ANTHROPIC_API_KEY WECOM_BUNDLE_ID; do
        if grep -q "^${k}=" .env 2>/dev/null; then
            val_len=$(grep "^${k}=" .env | cut -d= -f2- | wc -c | tr -d ' ')
            echo "  - ${k}: 已配置（长度 ${val_len}）"
        else
            warn "  - ${k}: 未配置"
        fi
    done
else
    warn ".env 不存在（首次使用可在管理界面配置）"
fi

# messages.db 状态
if [[ -f "messages.db" ]]; then
    db_size=$(ls -lh messages.db | awk '{print $5}')
    db_count=$(sqlite3 messages.db "SELECT COUNT(*) FROM messages;" 2>/dev/null || echo "?")
    ok "messages.db 存在（$db_size，共 $db_count 条记录）"
else
    warn "messages.db 不存在（首次运行会自动创建）"
fi

# daemon 进程状态
if [[ -f ".daemon.pid" ]]; then
    pid=$(cat .daemon.pid)
    if ps -p "$pid" >/dev/null 2>&1; then
        ok "守护进程运行中（PID=$pid）"
    else
        warn "守护进程未运行（.daemon.pid 指向的 $pid 已退出）"
    fi
else
    warn "守护进程未启动（可在管理界面启动）"
fi

# daemon.log 尾部
if [[ -f "daemon.log" && -s "daemon.log" ]]; then
    echo -e "\n${CYAN}── daemon.log 最近 20 行 ──${NC}"
    tail -20 daemon.log | sed 's/^/  /'
    echo -e "${CYAN}───────────────────────────${NC}\n"
else
    warn "daemon.log 为空或不存在（守护进程从未产生日志 → 可能未启动，或日志未写入文件）"
fi

# 辅助功能权限提示（无法程序化查询，但给出检查命令）
echo "辅助功能权限检查：若守护进程无法读取企业微信窗口，请在"
echo "  系统设置 → 隐私与安全性 → 辅助功能 中勾选 Terminal / Python"

# 端口检查
PORT=8501
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "端口 $PORT 已被占用，尝试清理旧进程"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>&1 | sed 's/^/  /'
    lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 2>/dev/null || true
    sleep 1
fi

echo -e "\n${GREEN}✓ 管理界面地址：${CYAN}http://localhost:${PORT}${NC}"
echo -e "${GREEN}✓ 调试日志：${CYAN}$(pwd)/$LOG_FILE${NC}"
echo -e "${GREEN}✓ 按 Ctrl+C 停止服务${NC}\n"

(sleep 3 && open "http://localhost:${PORT}") &

streamlit run admin/app.py --server.port "$PORT" --server.headless true

echo
read -p "服务已停止，按回车键关闭…"

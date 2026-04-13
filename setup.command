#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 企业微信自动回复 — 首次安装向导
# 双击此文件即可运行（macOS Finder）
# ──────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

# ── 调试日志：所有输出同时写入 debug-setup.log ─────────────────
LOG_FILE="debug-setup.log"
exec > >(tee "$LOG_FILE") 2>&1
echo "===== setup.command 运行于 $(date '+%Y-%m-%d %H:%M:%S') ====="

# ── 颜色 ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step() { echo -e "\n${BLUE}${BOLD}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
die()  {
    echo -e "\n${RED}${BOLD}安装中止：$1${NC}"
    echo -e "${YELLOW}完整日志已保存至：$(pwd)/$LOG_FILE${NC}"
    echo -e "${YELLOW}如需帮助，请把该文件发给技术支持。${NC}\n"
    read -rp "按回车键关闭…"
    exit 1
}

echo -e "\n${CYAN}${BOLD}╔══════════════════════════════════════╗"
echo -e      "║  企业微信自动回复 — 首次安装向导     ║"
echo -e      "╚══════════════════════════════════════╝${NC}\n"

# ── 步骤 0：环境自检（自动采集诊断信息）──────────────────────
step "步骤 0 / 4：环境自检"
echo "  工作目录：$(pwd)"
echo "  用户：$(whoami)"
echo "  macOS：$(sw_vers -productVersion 2>/dev/null || echo unknown)"
echo "  架构：$(uname -m)"
echo "  Shell：$SHELL"
echo "  系统 Python：$(command -v python3 || echo '未找到') ($(python3 --version 2>&1 || true))"
echo "  网络连通性：$(curl -fsSI --max-time 5 https://astral.sh >/dev/null 2>&1 && echo OK || echo FAIL)"

# 磁盘空间（至少 500MB）
AVAIL_KB=$(df -k . | awk 'NR==2 {print $4}')
if [[ -n "$AVAIL_KB" && "$AVAIL_KB" -lt 512000 ]]; then
    warn "可用磁盘空间不足 500MB，可能影响依赖安装"
else
    ok "磁盘空间充足"
fi

# Xcode Command Line Tools（编译 pyobjc 时需要）
if xcode-select -p &>/dev/null; then
    ok "Xcode Command Line Tools 已安装"
else
    warn "未检测到 Xcode Command Line Tools，pyobjc 编译可能失败"
    warn "如果后续失败请运行：xcode-select --install"
fi

# ── 步骤 1：安装 uv（Python 版本 + 包管理器）────────────────
step "步骤 1 / 4：安装 uv（Python 环境管理）"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if command -v uv &>/dev/null; then
    ok "uv 已安装：$(uv --version)"
else
    warn "正在安装 uv…（约 10 秒，需要网络）"
    curl -LsSf https://astral.sh/uv/install.sh | sh \
        || die "uv 安装失败，请检查网络后重试"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv &>/dev/null || die "uv 安装后仍找不到，请重启终端后再试"
    ok "uv 安装完成：$(uv --version)"
fi

# ── 步骤 2：创建 Python 3.12 虚拟环境 ───────────────────────
step "步骤 2 / 4：创建 Python 3.12 虚拟环境"

if [[ -d ".venv" && ! -f ".venv/bin/activate" ]]; then
    warn "检测到不完整的 .venv（上次安装中断），正在清理…"
    rm -rf .venv
fi

if [[ -d ".venv" ]]; then
    ok "虚拟环境已存在，跳过创建"
else
    warn "正在下载并安装 Python 3.12…（首次约 1-2 分钟）"
    uv venv .venv --python 3.12 \
        || die "创建虚拟环境失败，请检查网络后重试"
    ok "Python 3.12 虚拟环境创建完成"
fi

source .venv/bin/activate
ok "Python 就绪：$(python --version) @ $(which python)"

# ── 步骤 3：安装依赖 ─────────────────────────────────────────
step "步骤 3 / 4：安装依赖（约 1-3 分钟）"

uv pip install -r requirements.txt \
    || die "核心依赖安装失败，请检查网络后重试"
ok "核心依赖安装完成"

if uv pip install -r requirements-mac.txt; then
    ok "macOS 辅助功能依赖安装完成"
else
    warn "macOS 辅助功能依赖安装失败（atomacos/pyobjc-core）"
    warn "管理界面可正常使用，但守护进程 run.py 暂时无法启动"
    warn "可稍后手动运行：uv pip install -r requirements-mac.txt"
fi

# 依赖快照（便于排障）
echo -e "\n已安装包清单："
uv pip list 2>&1 | sed 's/^/  /'

# ── 步骤 3.5：首次运行复制 .env ───────────────────────────────
if [[ ! -f ".env" && -f ".env.example" ]]; then
    cp .env.example .env
    ok ".env 已从 .env.example 创建，稍后在管理界面完成配置"
fi

# ── 步骤 4：启动管理界面 ─────────────────────────────────────
step "步骤 4 / 4：启动管理界面"
PORT=8501

# 检查端口占用
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "端口 $PORT 已被占用，将尝试停止旧进程"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>&1 | sed 's/^/  /'
    lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN | xargs -r kill -9 2>/dev/null || true
    sleep 1
fi

echo -e "\n${GREEN}${BOLD}══════════════════════════════════════"
echo -e "  安装完成！正在启动管理界面…"
echo -e "══════════════════════════════════════${NC}"
echo -e "  地址：${CYAN}http://localhost:${PORT}${NC}"
echo -e "  调试日志：${CYAN}$(pwd)/$LOG_FILE${NC}"
echo -e "  关闭此窗口即可停止服务\n"

(sleep 3 && open "http://localhost:${PORT}") &

streamlit run admin/app.py --server.port "$PORT" --server.headless true

echo
read -rp "服务已停止，按回车键关闭…"

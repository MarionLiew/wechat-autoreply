#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 从模板生成 plist 并注册到 launchd（开机自启）
# 用法：bash scripts/launchd/install_launchd.sh [install|uninstall]
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/com.marionliew.wechat-autoreply.plist.template"

# label 自动取项目目录名，避免多实例冲突
LABEL="com.$(whoami).wechat-autoreply"
PLIST_NAME="$LABEL.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"

case "${1:-install}" in
    uninstall)
        if launchctl list "$LABEL" &>/dev/null; then
            launchctl unload "$PLIST_PATH" 2>/dev/null || true
            echo "已卸载 launchd 服务：$LABEL"
        fi
        rm -f "$PLIST_PATH"
        echo "已删除 $PLIST_PATH"
        ;;
    install)
        if [[ ! -f "$TEMPLATE" ]]; then
            echo "错误：找不到模板 $TEMPLATE" >&2
            exit 1
        fi

        # 从模板替换占位符
        sed \
            -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
            -e "s|__LABEL__|$LABEL|g" \
            "$TEMPLATE" > "$PLIST_PATH"

        echo "已生成 $PLIST_PATH"

        # 先卸载旧的（如果存在）
        launchctl unload "$PLIST_PATH" 2>/dev/null || true

        # 注册
        launchctl load -w "$PLIST_PATH"
        echo "已注册 launchd 服务：$LABEL"
        echo "登录后将自动启动，或手动运行：launchctl start $LABEL"
        ;;
    *)
        echo "用法：$0 [install|uninstall]" >&2
        exit 1
        ;;
esac

"""
企业微信 Mac 客户端 AX 选择器集中定义。

把散落在 mac_watcher 里对 AX 树的结构假设集中在这里，企微版本升级时
只改这个文件；失败时自动触发 dump 帮助定位。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 上次 dump 时间（进程内），避免选择器失败时频繁 dump
_last_dump_ts: float = 0.0
_DUMP_COOLDOWN_SEC = 60.0
_DUMP_OUTPUT = Path(__file__).parent.parent / "ax_tree_failure.txt"


def _safe_children(element) -> list:
    try:
        return element.AXChildren or []
    except Exception:
        return []


def deep_find_first(root, role: str, max_depth: int = 10):
    """DFS 返回第一个 AXRole == role 的节点；None 表示找不到。"""
    if max_depth < 0:
        return None
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            return root
    except Exception:
        pass
    for child in _safe_children(root):
        found = deep_find_first(child, role, max_depth - 1)
        if found is not None:
            return found
    return None


def deep_find_all(root, role: str, max_depth: int = 10) -> list:
    """DFS 返回所有 AXRole == role 的节点。"""
    out: list = []
    if max_depth < 0:
        return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            out.append(root)
    except Exception:
        pass
    for child in _safe_children(root):
        out.extend(deep_find_all(child, role, max_depth - 1))
    return out


# ────────────────────────────────────────────────────────────────
# 选择器：失败时自动 dump AX 树到 ax_tree_failure.txt
# ────────────────────────────────────────────────────────────────

def _maybe_dump(reason: str) -> None:
    global _last_dump_ts
    now = time.time()
    if now - _last_dump_ts < _DUMP_COOLDOWN_SEC:
        return
    _last_dump_ts = now
    try:
        result = subprocess.run(
            [sys.executable, "dump_ax_tree.py"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=15,
        )
        _DUMP_OUTPUT.write_text(
            f"=== 触发原因: {reason} ===\n{result.stdout}\n{result.stderr}",
            encoding="utf-8",
        )
        logger.warning("AX 选择器失败，已 dump 到 %s（%s）", _DUMP_OUTPUT.name, reason)
    except Exception as exc:
        logger.debug("自动 dump 失败：%s", exc)


def find_conversation_table(window) -> Any | None:
    """会话列表容器：窗口下第一个 AXTable。"""
    table = deep_find_first(window, "AXTable", max_depth=10)
    if table is None:
        _maybe_dump("未找到 AXTable（会话列表）")
    return table


def find_conversation_rows(table) -> list:
    """AXTable 的直接 AXRow 子节点。"""
    return [
        ch for ch in _safe_children(table)
        if str(getattr(ch, "AXRole", "") or "") == "AXRow"
    ]


def row_unread_count(row) -> int:
    """会话行未读数：row 下任一 AXButton，其 AXTitle 为数字字符串。"""
    for btn in deep_find_all(row, "AXButton", max_depth=5):
        title = str(getattr(btn, "AXTitle", "") or "").strip()
        if title.isdigit():
            n = int(title)
            if n > 0:
                return n
    return 0


def row_static_texts(row) -> list[str]:
    """会话行中所有非空 AXStaticText 的 AXValue 列表（按 DFS 顺序）。"""
    out: list[str] = []
    for t in deep_find_all(row, "AXStaticText", max_depth=5):
        v = str(getattr(t, "AXValue", "") or "").strip()
        if v:
            out.append(v)
    return out


def find_chat_text_area(window) -> Any | None:
    """
    聊天输入框：深度遍历窗口下所有 AXTextArea；取最后一个非 BOT 标签。
    """
    areas = deep_find_all(window, "AXTextArea", max_depth=12)
    candidates = []
    for ta in areas:
        val = str(getattr(ta, "AXValue", "") or "")
        if val.strip() == "BOT":
            continue
        candidates.append(ta)
    if not candidates:
        _maybe_dump("未找到输入框 AXTextArea")
        return None
    return candidates[-1]


def find_chat_web_area(window) -> Any | None:
    """
    聊天消息面板：倒序扫描 AXScrollArea，取内部含 AXWebArea 的那个；
    返回 AXWebArea 本身（内含所有消息 AXStaticText）。
    """
    scroll_areas = deep_find_all(window, "AXScrollArea", max_depth=12)
    for sa in reversed(scroll_areas):
        web = deep_find_first(sa, "AXWebArea", max_depth=3)
        if web is not None:
            return web
    # 回退：最后一个 AXScrollArea
    if scroll_areas:
        return scroll_areas[-1]
    _maybe_dump("未找到聊天消息面板 AXScrollArea")
    return None


def press_conversation_row(row) -> bool:
    """
    点击会话行切换到该会话。依次尝试：AXCell.Press → AXSelected → 鼠标坐标点击。
    """
    for target in (row, deep_find_first(row, "AXCell", max_depth=2)):
        if target is None:
            continue
        for action in ("Press", "AXPress"):
            fn = getattr(target, action, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except Exception:
                    pass
    try:
        row.AXSelected = True
        return True
    except Exception:
        pass
    try:
        import Quartz
        pos = row.AXPosition
        size = row.AXSize
        cx = pos.x + size.width / 2
        cy = pos.y + size.height / 2
        ev_down = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, (cx, cy), Quartz.kCGMouseButtonLeft)
        ev_up = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, (cx, cy), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev_down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev_up)
        return True
    except Exception as exc:
        logger.debug("鼠标点击会话行失败：%s", exc)
    return False

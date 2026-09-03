from __future__ import annotations

"""Keyword rule engine with hot-reload from rules.json."""

import json
import random
import re
from pathlib import Path

RULES_FILE = Path(__file__).parent.parent / "rules.json"


def _load_rules() -> list[dict]:
    with open(RULES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return sorted(
        [r for r in data.get("rules", []) if r.get("enabled", True)],
        key=lambda r: r.get("priority", 999),
    )


def _pick_reply(rule: dict) -> str:
    """从规则中取回复文本；支持 replies 列表随机选一条。"""
    replies = rule.get("replies")
    if replies:
        return random.choice(replies)
    return rule.get("reply", "")


def match(message: str) -> str | None:
    """Return the reply text for the first matching rule, or None.

    对多条消息合并的文本 (\n 分隔)：
    - exact：按整段文本比较（通常只匹配单条消息时才有意义）
    - contains：子串，自然支持多行
    - regex：默认启用 MULTILINE（^/$ 按行）；rule["ignore_case"]=True 时加 IGNORECASE

    max_length 字段（可选）：若消息长度超过此值，规则不匹配。
    用���防止长模板消息（如客服自动回复）误命中短回复规则（如"谢谢"→"不客气～"）。
    """
    for rule in _load_rules():
        # 消息长度上限：防止长模板误命中短回复规则
        max_len = rule.get("max_length")
        if max_len is not None and len(message) > max_len:
            continue

        match_type = rule.get("match_type")
        if match_type == "exact" and message == rule.get("keyword"):
            return _pick_reply(rule)
        elif match_type == "contains":
            kw = rule.get("keyword") or ""
            if rule.get("ignore_case"):
                if kw.lower() in message.lower():
                    return _pick_reply(rule)
            elif kw in message:
                return _pick_reply(rule)
        elif match_type == "regex" and rule.get("pattern"):
            flags = re.MULTILINE
            if rule.get("ignore_case"):
                flags |= re.IGNORECASE
            try:
                if re.search(rule["pattern"], message, flags=flags):
                    return _pick_reply(rule)
            except re.error:
                continue
    return None

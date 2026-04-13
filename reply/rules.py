"""Keyword rule engine with hot-reload from rules.json."""

import json
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


def match(message: str) -> str | None:
    """Return the reply text for the first matching rule, or None.

    对多条消息合并的文本 (\n 分隔)：
    - exact：按整段文本比较（通常只匹配单条消息时才有意义）
    - contains：子串，自然支持多行
    - regex：默认启用 MULTILINE（^/$ 按行）；rule["ignore_case"]=True 时加 IGNORECASE
    """
    for rule in _load_rules():
        match_type = rule.get("match_type")
        if match_type == "exact" and message == rule.get("keyword"):
            return rule["reply"]
        elif match_type == "contains":
            kw = rule.get("keyword") or ""
            if rule.get("ignore_case"):
                if kw.lower() in message.lower():
                    return rule["reply"]
            elif kw in message:
                return rule["reply"]
        elif match_type == "regex" and rule.get("pattern"):
            flags = re.MULTILINE
            if rule.get("ignore_case"):
                flags |= re.IGNORECASE
            try:
                if re.search(rule["pattern"], message, flags=flags):
                    return rule["reply"]
            except re.error:
                continue
    return None

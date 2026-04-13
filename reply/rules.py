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
    """Return the reply text for the first matching rule, or None."""
    for rule in _load_rules():
        match_type = rule.get("match_type")
        if match_type == "exact" and message == rule.get("keyword"):
            return rule["reply"]
        elif match_type == "contains" and rule.get("keyword") in message:
            return rule["reply"]
        elif match_type == "regex" and rule.get("pattern"):
            if re.search(rule["pattern"], message):
                return rule["reply"]
    return None

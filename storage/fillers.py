"""废话库：读写 fillers.json，提供不重复随机抽取。"""

import json
import random
from collections import deque
from pathlib import Path

FILLERS_FILE = Path(__file__).parent.parent / "fillers.json"

# 全局最近使用（兜底）
_used: deque[int] = deque()
# 按 sender 的最近使用，避免同一客户连续看到相同 filler
_used_by_sender: dict[str, deque[int]] = {}


def load_fillers() -> list[str]:
    """读取废话库，不存在时返回空列表。"""
    if not FILLERS_FILE.exists():
        return []
    with open(FILLERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data.get("fillers", []) if isinstance(s, str) and s.strip()]


def save_fillers(fillers: list[str]) -> None:
    """将废话列表写入 fillers.json。"""
    with open(FILLERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"fillers": fillers}, f, ensure_ascii=False, indent=2)


def pick_filler(sender_id: str | None = None, window: int = 5) -> str | None:
    """
    从废话库随机选一条，避免重复。

    - 传 sender_id：按该客户最近 `window` 次回复去重（同一客户看到的更多样）
    - 不传：走全局去重
    """
    fillers = load_fillers()
    if not fillers:
        return None

    n = len(fillers)
    if sender_id:
        dq = _used_by_sender.setdefault(sender_id, deque())
        exclude_count = min(n - 1, window)
        recent = set(list(dq)[-exclude_count:]) if exclude_count > 0 else set()
    else:
        dq = _used
        exclude_count = min(n // 2, 3)
        recent = set(list(dq)[-exclude_count:]) if exclude_count > 0 else set()

    candidates = [i for i in range(n) if i not in recent] or list(range(n))
    idx = random.choice(candidates)
    dq.append(idx)
    while len(dq) > max(n, 10):
        dq.popleft()
    return fillers[idx]

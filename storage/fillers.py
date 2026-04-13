"""废话库：读写 fillers.json，提供不重复随机抽取。"""

import json
import random
from collections import deque
from pathlib import Path

FILLERS_FILE = Path(__file__).parent.parent / "fillers.json"

# 模块级已用索引队列（进程内持久，不跨重启）
_used: deque[int] = deque()


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


def pick_filler() -> str | None:
    """
    从废话库随机选一条，避免与最近使用的重复。

    算法：
    1. 排除最近使用的 min(len//2, 3) 条索引作为候选池
    2. 若候选池为空（库太小），直接全量随机
    3. 从候选池中 random.choice，记录到 _used deque
    """
    fillers = load_fillers()
    if not fillers:
        return None

    n = len(fillers)
    exclude_count = min(n // 2, 3)
    recent = set(list(_used)[-exclude_count:]) if exclude_count > 0 else set()
    candidates = [i for i in range(n) if i not in recent]

    if not candidates:
        candidates = list(range(n))

    idx = random.choice(candidates)
    _used.append(idx)
    # 只保留最近 max(n, 10) 条记录，避免无限增长
    while len(_used) > max(n, 10):
        _used.popleft()

    return fillers[idx]

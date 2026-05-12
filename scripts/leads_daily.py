"""查询经营线索每日成功发送条数。

用法：
    python scripts/leads_daily.py             # 今天
    python scripts/leads_daily.py 2026-05-12  # 指定某天
    python scripts/leads_daily.py --all       # 历史每天汇总
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import leads_log  # noqa: E402

_LOG_PATH = ROOT / "storage" / "leads_sent.jsonl"


def all_days() -> list[tuple[str, int, list[str]]]:
    if not _LOG_PATH.exists():
        return []
    by_day: dict[str, list[str]] = defaultdict(list)
    with _LOG_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            day = rec.get("ts", "")[:10]
            tr = rec.get("traveler", "")
            if day:
                by_day[day].append(tr)
    return sorted([(d, len(ts), ts) for d, ts in by_day.items()])


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--all":
        rows = all_days()
        if not rows:
            print("（暂无记录）")
            return 0
        print(f"{'日期':<12} {'条数':>4}  旅客")
        for day, n, names in rows:
            sample = "、".join(names[:5]) + ("..." if len(names) > 5 else "")
            print(f"{day:<12} {n:>4}  {sample}")
        return 0

    if len(argv) >= 2:
        day = argv[1]
    else:
        day = datetime.now().strftime("%Y-%m-%d")
    n = leads_log.count_on(day)
    print(f"{day} 成功发送经营线索：{n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

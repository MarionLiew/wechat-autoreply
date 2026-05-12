"""经营线索成功发送的持久化日志（按日统计）。

落地为 jsonl：每行 {"ts": ISO时间, "traveler": "...", "hash": "..."}。
读时按本地日期分组计数。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "storage" / "leads_sent.jsonl"


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def record_lead_sent(traveler: str, lead_hash: str) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "traveler": traveler or "",
        "hash": (lead_hash or "")[:12],
    }
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def count_on(date_str: str) -> int:
    """date_str 形如 '2026-05-12'。返回该日成功发送条数。"""
    if not _LOG_PATH.exists():
        return 0
    n = 0
    with _LOG_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("ts", "")
            if ts.startswith(date_str):
                n += 1
    return n


def count_today() -> int:
    return count_on(_today_str())

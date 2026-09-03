"""学习语料库：被动捕获「真人客户经理纠正/补答」的 QA 候选。

飞轮第①步——燃料采集。bot 打开会话读消息时，顺手从聊天面板右侧（我方）找出
**真人手打、且不在 bot 已发集合里**的回复，配上它前面的客户问题，存成候选：

    {ts, customer, q, a, bot_reply, source, status, qhash}

- source = "human_override"（真人在 bot 之后补/改）
- status = "pending"（等你评分/审核；通过后才增量嵌入进 RAG）
- bot_reply = 该问题下 bot 当时发的回复（若有），作负样本参考

只追加、按 qhash 去重。审核与增量嵌入是后续步骤，本模块只负责采集。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

LEARNED_QA_FILE = Path(__file__).parent.parent / "data" / "learned_qa.jsonl"

# 进程内已见 qhash 缓存，避免重复落盘
_seen: set[str] | None = None


def _qhash(customer: str, q: str, a: str) -> str:
    return hashlib.sha256(f"{customer}\x00{q}\x00{a}".encode()).hexdigest()[:16]


def _load_seen() -> set[str]:
    global _seen
    if _seen is not None:
        return _seen
    seen: set[str] = set()
    if LEARNED_QA_FILE.exists():
        with LEARNED_QA_FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["qhash"])
                except Exception:
                    continue
    _seen = seen
    return seen


def save_candidate(
    customer: str,
    q: str,
    a: str,
    bot_reply: str = "",
    source: str = "human_override",
    status: str = "pending",
) -> bool:
    """落一条候选。已存在（同 customer+q+a）返回 False，新增返回 True。

    status：pending（待审，①被动捕获默认）/ approved（已采纳，评分时直接给）。
    """
    q = (q or "").strip()
    a = (a or "").strip()
    if not q or not a:
        return False
    seen = _load_seen()
    h = _qhash(customer, q, a)
    if h in seen:
        return False
    LEARNED_QA_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "customer": customer,
        "q": q,
        "a": a,
        "bot_reply": bot_reply,
        "source": source,
        "status": status,
        "qhash": h,
    }
    with LEARNED_QA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    seen.add(h)
    return True


def load_all(status: str | None = None) -> list[dict]:
    """读取全部候选（可按 status 过滤），按写入顺序返回。"""
    if not LEARNED_QA_FILE.exists():
        return []
    out: list[dict] = []
    with LEARNED_QA_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if status is None or r.get("status") == status:
                out.append(r)
    return out


def update_status(qhash: str, status: str, new_a: str | None = None) -> bool:
    """按 qhash 改某条候选的状态（approved/rejected），可同时修订答案 a。重写整个文件。"""
    if not LEARNED_QA_FILE.exists():
        return False
    records, hit = [], False
    with LEARNED_QA_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("qhash") == qhash:
                r["status"] = status
                if new_a is not None and new_a.strip():
                    r["a"] = new_a.strip()
                hit = True
            records.append(r)
    if hit:
        with LEARNED_QA_FILE.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return hit


# ── bot 回复评分标记（记哪些 messages.db 里的 bot 回复已被你评过，避免重复出现）──
_RATED_FILE = Path(__file__).parent.parent / "data" / "rated_replies.txt"


def _reply_key(customer: str, q: str, bot_reply: str) -> str:
    return _qhash(customer, q, bot_reply)


def rated_reply_keys() -> set[str]:
    if not _RATED_FILE.exists():
        return set()
    return {ln.strip() for ln in _RATED_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}


def mark_reply_rated(customer: str, q: str, bot_reply: str) -> None:
    _RATED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _RATED_FILE.open("a", encoding="utf-8") as f:
        f.write(_reply_key(customer, q, bot_reply) + "\n")

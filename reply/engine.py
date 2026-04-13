"""
混合回复引擎：三层回退优先级。

  层 1 — 关键词规则（rules.json，始终启用）
  层 2 — 废话库（filler_enabled=True 时启用）
  层 3 — 大模型 LLM（llm_enabled=True 且 key 非空时启用）

全部跳过时返回 source="none"，调用方应静默处理（不发送、不记录）。
"""

import time
from collections import defaultdict, deque

from config import settings
from reply import claude_client, rules
from storage import fillers

# 每个 sender 的 LLM 调用时间戳（秒），用于滑动窗口速率限制
_llm_call_times: dict[str, deque] = defaultdict(deque)


def _llm_allowed(sender_id: str) -> bool:
    """判断该 sender 当前是否允许调用 LLM（60 秒窗口内不超过 llm_rate_limit_per_minute）。"""
    limit = settings.llm_rate_limit_per_minute
    if limit <= 0:
        return True
    now = time.time()
    dq = _llm_call_times[sender_id]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


def process_message(
    text: str,
    sender_id: str | None = None,
    context: list[str] | None = None,
) -> dict:
    """
    返回 dict，包含：
      source: "rules" | "filler" | "claude" | "none"
      content: 回复文本（source="none" 时为空字符串）
    """
    # 层 1：关键词规则
    reply = rules.match(text)
    if reply is not None:
        return {"source": "rules", "content": reply}

    # 层 2：废话库（按 sender 去重）
    if settings.filler_enabled:
        filler = fillers.pick_filler(
            sender_id=sender_id,
            window=settings.filler_antirepeat_window,
        )
        if filler is not None:
            return {"source": "filler", "content": filler}

    # 层 3：大模型（按 sender 限流，超限改走 filler 兜底）
    if sender_id and not _llm_allowed(sender_id):
        filler = fillers.pick_filler(sender_id=sender_id)
        if filler is not None:
            return {"source": "filler_ratelimited", "content": filler}
        return {"source": "none", "content": ""}

    reply = claude_client.generate(text, context=context)
    if reply is not None:
        return {"source": "claude", "content": reply}

    return {"source": "none", "content": ""}

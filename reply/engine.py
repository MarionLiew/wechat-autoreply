"""
混合回复引擎：三层回退优先级。

  层 1 — 关键词规则（rules.json，始终启用）
  层 2 — 废话库（filler_enabled=True 时启用）
  层 3 — 大模型 LLM（llm_enabled=True 且 key 非空时启用）

全部跳过时返回 source="none"，调用方应静默处理（不发送、不记录）。
"""

from config import settings
from reply import claude_client, rules
from storage import fillers


def process_message(text: str) -> dict:
    """
    返回 dict，包含：
      source: "rules" | "filler" | "claude" | "none"
      content: 回复文本（source="none" 时为空字符串）
    """
    # 层 1：关键词规则
    reply = rules.match(text)
    if reply is not None:
        return {"source": "rules", "content": reply}

    # 层 2：废话库
    if settings.filler_enabled:
        filler = fillers.pick_filler()
        if filler is not None:
            return {"source": "filler", "content": filler}

    # 层 3：大模型
    reply = claude_client.generate(text)
    if reply is not None:
        return {"source": "claude", "content": reply}

    # 全部跳过
    return {"source": "none", "content": ""}

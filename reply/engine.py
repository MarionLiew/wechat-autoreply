from __future__ import annotations

"""
混合回复引擎：多层回退优先级。

  层 1 — 关键词规则（rules.json，始终启用）
  层 2 — RAG 话术库（rag_enabled=True 时启用）
            top-1 score ≥ rag_direct_threshold → 直接复用历史回复（A 路径）
            top-1 score ≥ rag_fewshot_threshold → 检索结果作 few-shot 进 LLM（B 路径）
            否则跳过此层
  层 3 — 废话库（filler_enabled=True 时启用）
  层 4 — 大模型 LLM（llm_enabled=True 且 key 非空时启用）

全部跳过时返回 source="none"，调用方应静默处理（不发送、不记录）。
"""

import logging
import time
from collections import defaultdict, deque

from config import settings
from reply import claude_client, rules
from storage import fillers

logger = logging.getLogger(__name__)

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
    history: list[dict] | None = None,
) -> dict:
    """
    text: 当前轮合并后的客户消息文本（已过滤我方回声）
    context: 本轮客户的各条消息（list[str]），用于 LLM 参考
    history: 历史对话（list[{"role": "user"|"assistant", "content": "..."}]）
    """
    reply = rules.match(text)
    if reply is not None:
        return {"source": "rules", "content": reply}

    # ── RAG 层：从客户经理历史话术库检索 ─────────────────────
    rag_hits: list[dict] = []
    if settings.rag_enabled:
        try:
            from reply import rag as rag_module
            retriever = rag_module.get_retriever(settings.rag_manager)
            rag_hits = retriever.search(
                text,
                k=settings.rag_topk,
                min_score=settings.rag_fewshot_threshold,
            )
        except FileNotFoundError as exc:
            logger.warning("RAG 索引未就绪，跳过：%s", exc)
        except Exception as exc:
            logger.warning("RAG 检索异常，跳过：%s", exc)

        if rag_hits:
            top1 = rag_hits[0]
            # A 路径：高置信直接复用历史回复
            #   - 安全（无人名）→ 直接用
            #   - 含人名但能替换成当前客户名 → 替换后用（保留罗响"X哥/X姐"称呼风格）
            if top1["score"] >= settings.rag_direct_threshold:
                a = top1["a"]
                if top1.get("safe_for_direct"):
                    logger.info(
                        "RAG A 直接命中 score=%.3f hist_q=%r",
                        top1["score"], top1["q"][:50],
                    )
                    return {"source": "rag_direct", "content": a, "rag_score": top1["score"]}
                # 尝试名字替换：把历史客户名换成当前客户名
                if sender_id:
                    from reply import rag as rag_module
                    new_a, did_sub = rag_module.substitute_customer_name(
                        a, top1.get("customer", ""), sender_id,
                    )
                    if did_sub and rag_module.is_safe_after_substitution(new_a, sender_id):
                        logger.info(
                            "RAG A 名字替换命中 score=%.3f hist_客户=%r→%r",
                            top1["score"], top1.get("customer"), sender_id,
                        )
                        return {"source": "rag_direct_sub", "content": new_a, "rag_score": top1["score"]}
            # B 路径：作为 few-shot 注入 LLM（跳过 filler，直接走 LLM）
            # 同时也走下面的 sender 速率检查
            if sender_id and not _llm_allowed(sender_id):
                logger.info("RAG B 路径触发但 LLM 速率超限，跳过本次回复")
                return {"source": "none", "content": ""}
            reply = claude_client.generate(
                text,
                context=context,
                history=history,
                few_shot=rag_hits,
                customer_name=sender_id,
            )
            if reply is not None:
                logger.info(
                    "RAG few-shot → LLM 生成 manager=%s top_score=%.3f",
                    settings.rag_manager, rag_hits[0]["score"],
                )
                return {"source": "rag_fewshot", "content": reply}
            # LLM 调用失败 → 继续下面的 filler/原 LLM 兜底

    if settings.filler_enabled:
        filler = fillers.pick_filler(
            sender_id=sender_id,
            window=settings.filler_antirepeat_window,
        )
        if filler is not None:
            return {"source": "filler", "content": filler}

    if sender_id and not _llm_allowed(sender_id):
        filler = fillers.pick_filler(sender_id=sender_id)
        if filler is not None:
            return {"source": "filler_ratelimited", "content": filler}
        return {"source": "none", "content": ""}

    reply = claude_client.generate(
        text,
        context=context,
        history=history,
        few_shot=rag_hits if rag_hits else None,
    )
    if reply is not None:
        return {
            "source": "llm_rag" if rag_hits else "llm",
            "content": reply,
        }

    # 保底：mimo 与 moonshot 兜底都连不上时，不再静默跳过，
    # 发一句安抚话术让客户知道消息已收到、有人会跟进。
    fallback = settings.llm_fallback_reply.strip()
    if fallback:
        logger.warning("LLM 全部失败，发送保底话术 [%s]", sender_id)
        return {"source": "fallback", "content": fallback}

    return {"source": "none", "content": ""}

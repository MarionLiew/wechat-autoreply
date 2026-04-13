"""
LLM 客户端：支持多 provider，统一接口。

支持的 provider：
  anthropic — 原生 Anthropic SDK
  openai    — OpenAI 官方
  moonshot  — 月之暗面（OpenAI 兼容）
  zhipu     — 智谱 AI（OpenAI 兼容）
  qwen      — 阿里百炼（OpenAI 兼容）
  custom    — 自定义 base_url（OpenAI 兼容）
"""

import logging

from config import settings

logger = logging.getLogger(__name__)

# provider → 默认 base_url（None = 使用 SDK 内置地址）
_PROVIDER_BASE_URLS: dict[str, str | None] = {
    "anthropic": None,
    "openai": None,
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "custom": None,  # 由 llm_base_url 覆盖
}

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    provider = settings.llm_provider
    api_key = settings.effective_api_key

    if provider == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key)
    else:
        import openai
        base_url = settings.llm_base_url or _PROVIDER_BASE_URLS.get(provider)
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = openai.OpenAI(**kwargs)

    return _client


def reset_client() -> None:
    """重置客户端单例（配置更新后调用）。"""
    global _client
    _client = None


def generate(
    message: str,
    context: list[str] | None = None,
    history: list[dict] | None = None,
) -> str | None:
    """
    调用大模型生成回复。

    message: 当前轮客户消息（可能是多条合并的字符串）
    context: 本轮客户的逐条消息列表，用于 LLM 在单 user 消息里看清分隔
    history: 此客户的历史对话 list[{"role":"user"|"assistant","content":"..."}]
             — 按时间升序排列，不含本轮 message
    """
    if not settings.llm_enabled:
        return None
    if not settings.effective_api_key:
        return None

    try:
        provider = settings.llm_provider
        model = settings.effective_model
        client = _get_client()

        # 若 context 比 message 更细致，用分条形式替换 message
        if context and len(context) > 1:
            message = "\n".join(f"- {c}" for c in context)

        # 构造 messages 数组（历史 + 当前）
        msgs_array: list[dict] = []
        if history:
            for h in history:
                role = h.get("role")
                content = h.get("content") or ""
                if role in ("user", "assistant") and content:
                    msgs_array.append({"role": role, "content": content})
        msgs_array.append({"role": "user", "content": message})

        if provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=settings.system_prompt,
                messages=msgs_array,
            )
            return response.content[0].text
        else:
            response = client.chat.completions.create(
                model=model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": settings.system_prompt},
                    *msgs_array,
                ],
            )
            return response.choices[0].message.content

    except Exception as exc:
        logger.error("LLM 调用失败（provider=%s）：%s", settings.llm_provider, exc)
        return None

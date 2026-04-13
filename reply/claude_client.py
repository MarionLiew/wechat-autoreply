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


def generate(message: str) -> str | None:
    """
    调用大模型生成回复。

    以下情况直接返回 None（静默跳过）：
    - llm_enabled=False
    - effective_api_key 为空
    """
    if not settings.llm_enabled:
        return None
    if not settings.effective_api_key:
        return None

    try:
        provider = settings.llm_provider
        model = settings.effective_model
        client = _get_client()

        if provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=settings.system_prompt,
                messages=[{"role": "user", "content": message}],
            )
            return response.content[0].text
        else:
            # OpenAI 兼容接口
            response = client.chat.completions.create(
                model=model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": settings.system_prompt},
                    {"role": "user", "content": message},
                ],
            )
            return response.choices[0].message.content

    except Exception as exc:
        logger.error("LLM 调用失败（provider=%s）：%s", settings.llm_provider, exc)
        return None

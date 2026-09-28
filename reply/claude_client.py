from __future__ import annotations

"""
LLM 客户端：支持多 provider，统一接口。

支持的 provider：
  anthropic — 原生 Anthropic SDK
  openai    — OpenAI 官方
  moonshot  — 月之暗面（OpenAI 兼容）
  zhipu     — 智谱 AI（OpenAI 兼容）
  qwen      — 阿里百炼（OpenAI 兼容）
  mimo      — 小米 MiMo（OpenAI 兼容）
  custom    — 自定义 base_url（OpenAI 兼容）
"""

import logging
import time

from config import settings
from reply.customer_address import address_instruction

logger = logging.getLogger(__name__)

# provider → 默认 base_url（None = 使用 SDK 内置地址）
_PROVIDER_BASE_URLS: dict[str, str | None] = {
    "anthropic": None,
    "openai": None,
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "mimo": None,  # 根据 key 前缀动态判断：tp- → Token Plan，sk- → 按量付费
    "custom": None,  # 由 llm_base_url 覆盖
}

_client = None


def _resolve_base_url(provider: str, api_key: str) -> str | None:
    """解析 provider 的 base_url，mimo 根据 key 前缀动态判断。"""
    if provider == "mimo":
        if api_key.startswith("tp-"):
            return "https://token-plan-cn.xiaomimimo.com/v1"
        return "https://api.xiaomimimo.com/v1"
    return _PROVIDER_BASE_URLS.get(provider)


def _http_client():
    """构造无视系统/环境代理的 httpx 客户端。

    mimo / moonshot 等都是国内端点，不应走用户机器上的 VPN 代理（如 Shadowrocket）。
    trust_env=False 让 httpx 忽略 macOS 系统代理和 http(s)_proxy 环境变量，直连出网，
    这样代理无论是否在线、指向哪个端口，都不影响自动回复。
    """
    import httpx
    return httpx.Client(trust_env=False)


def _openai_fallback(
    name: str,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    msgs_array: list[dict],
) -> str | None:
    """OpenAI 兼容的兜底调用（moonshot / openrouter 共用）。成功返回文本，失败返回 None。"""
    logger.warning("主 LLM 失败，切换 %s 兜底（model=%s）", name, model)
    try:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url, http_client=_http_client())
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "system", "content": system_prompt}, *msgs_array],
        )
        reply = resp.choices[0].message.content
        if reply:
            logger.info("%s 兜底成功", name)
            return reply
        logger.warning("%s 兜底返回空内容", name)
    except Exception as exc:
        logger.error("%s 兜底也失败：%s", name, str(exc)[:200])
    return None


def _get_client():
    global _client
    if _client is not None:
        return _client

    provider = settings.llm_provider
    api_key = settings.effective_api_key

    if provider == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key, http_client=_http_client())
    elif provider == "mimo":
        # mimo 的 Anthropic 兼容端点（/anthropic），用 Anthropic SDK
        import anthropic
        base_url = settings.llm_base_url or "https://token-plan-cn.xiaomimimo.com/anthropic"
        _client = anthropic.Anthropic(api_key=api_key, base_url=base_url, http_client=_http_client())
    else:
        import openai
        base_url = settings.llm_base_url or _resolve_base_url(provider, api_key)
        kwargs = {"api_key": api_key, "http_client": _http_client()}
        if base_url:
            kwargs["base_url"] = base_url
        _client = openai.OpenAI(**kwargs)

    return _client


def reset_client() -> None:
    """重置客户端单例（配置更新后调用）。"""
    global _client
    _client = None


def _extract_customer_name(sender_id: str | None) -> str:
    """从 sender_id（如 '周斌(男)-1234'）抽取干净的客户名 '周斌'。"""
    if not sender_id:
        return ""
    # 去性别括号、数字 ID 后缀
    name = sender_id.split("(")[0].split("（")[0].split("-")[0].strip()
    return name


def _extract_gender(sender_id: str | None) -> str | None:
    """从 sender_id（如 '周斌(男)-1234'）抽取性别 '男'/'女'，无性别信息返回 None。"""
    if not sender_id:
        return None
    if "男" in sender_id:
        return "男"
    if "女" in sender_id:
        return "女"
    return None


def _build_system_prompt(
    few_shot: list[dict] | None,
    customer_name: str | None = None,
) -> str:
    """系统提示：基础 prompt + 可选的 RAG few-shot 示例（注入风格） + 当前客户名提示。"""
    base = settings.system_prompt
    extras = []

    if few_shot:
        extras.append(
            "请参考以下「该客户经理过往真实对话」的风格回复——口吻、长度、emoji 使用习惯都要贴近示例。"
            "注意：示例里的亲昵称呼只是历史对话，必须遵守上述正式称呼规则，不要照搬。"
        )
        for i, ex in enumerate(few_shot, 1):
            q = (ex.get("q") or "").strip()
            # few-shot 用原始 a（含历史客户名，给 LLM 看完整风格）
            a = (ex.get("a_raw") or ex.get("a") or "").strip()
            if not q or not a:
                continue
            extras.append(f"\n示例 {i}:\n客户：{q}\n客户经理：{a}")

    if not extras:
        return base + "\n\n" + address_instruction(customer_name)
    # Repeat the higher-priority constraint after raw historical examples as well.
    return base + "\n\n" + "\n\n".join(extras) + "\n\n" + address_instruction(customer_name)


def generate(
    message: str,
    context: list[str] | None = None,
    history: list[dict] | None = None,
    few_shot: list[dict] | None = None,
    customer_name: str | None = None,
) -> str | None:
    """
    调用大模型生成回复。

    message: 当前轮客户消息（可能是多条合并的字符串）
    context: 本轮客户的逐条消息列表，用于 LLM 在单 user 消息里看清分隔
    history: 此客户的历史对话 list[{"role":"user"|"assistant","content":"..."}]
             — 按时间升序排列，不含本轮 message
    few_shot: RAG 检索到的同位客户经理过往 QA 样本 list[{"q":..., "a":...}]
             — 仅用于风格示范，注入到 system prompt 里
    """
    if not settings.llm_enabled:
        return None
    if not settings.effective_api_key:
        return None

    try:
        provider = settings.llm_provider
        model = settings.effective_model
        client = _get_client()
        system_prompt = _build_system_prompt(few_shot, customer_name)

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

        # 瞬时错误重试：最多 3 次，指数退避
        #   - 429 / overloaded / rate limit → 所有 provider 重试
        #   - 401 → 仅 mimo 重试（mimo 限流时也返回 401，与真实鉴权失败无法区分）
        last_exc = None
        for attempt in range(4):
            try:
                if provider in ("anthropic", "mimo"):
                    response = client.messages.create(
                        model=model,
                        max_tokens=1024,
                        system=system_prompt,
                        messages=msgs_array,
                    )
                    # mimo 返回 ThinkingBlock + TextBlock，取 TextBlock
                    for block in response.content:
                        if hasattr(block, "text"):
                            return block.text
                    return response.content[0].text
                else:
                    response = client.chat.completions.create(
                        model=model,
                        max_tokens=1024,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            *msgs_array,
                        ],
                    )
                    return response.choices[0].message.content
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                is_rate = "429" in msg or "overload" in msg.lower() or "rate" in msg.lower()
                is_mimo_401 = provider == "mimo" and "401" in msg
                if not is_rate and not is_mimo_401:
                    # 非限流错误（连接错误/超时等）：不重试，但仍落到下面的
                    # moonshot 兜底分支，而不是直接 raise 跳过兜底。
                    logger.warning("LLM 调用出错（非限流，转兜底）：%s", msg[:200])
                    break
                if attempt < 3:
                    wait = 2.0 * (attempt + 1)
                    logger.warning("LLM 限流（attempt=%d/%d），%.1fs 后重试：%s",
                                   attempt + 1, 4, wait, msg[:200])
                    time.sleep(wait)
        # 重试耗尽 → 兜底链：moonshot → openrouter（跳过与主 provider 相同的那层）
        if provider != "moonshot" and settings.moonshot_api_key:
            reply = _openai_fallback(
                "moonshot", settings.moonshot_api_key,
                "https://api.moonshot.cn/v1", "moonshot-v1-8k",
                system_prompt, msgs_array,
            )
            if reply:
                return reply
        if provider != "openrouter" and settings.openrouter_api_key:
            reply = _openai_fallback(
                "openrouter", settings.openrouter_api_key,
                "https://openrouter.ai/api/v1", settings.openrouter_model,
                system_prompt, msgs_array,
            )
            if reply:
                return reply
        raise last_exc

    except Exception as exc:
        logger.error("LLM 调用失败（provider=%s）：%s", settings.llm_provider, exc)
        return None

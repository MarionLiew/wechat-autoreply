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


def _extract_customer_name(sender_id: str | None) -> str:
    """从 sender_id（如 '周斌(男)-1234'）抽取干净的客户名 '周斌'。"""
    if not sender_id:
        return ""
    # 去性别括号、数字 ID 后缀
    name = sender_id.split("(")[0].split("（")[0].split("-")[0].strip()
    return name


def _build_system_prompt(
    few_shot: list[dict] | None,
    customer_name: str | None = None,
) -> str:
    """系统提示：基础 prompt + 可选的 RAG few-shot 示例（注入风格） + 当前客户名提示。"""
    base = settings.system_prompt
    extras = []

    if customer_name:
        clean_name = _extract_customer_name(customer_name)
        if clean_name:
            # 判断是中文名还是英文/拉丁名
            has_chinese = any("一" <= ch <= "鿿" for ch in clean_name)
            if has_chinese:
                first = clean_name[0]
                last = clean_name[-1] if len(clean_name) > 1 else clean_name
                extras.append(
                    f"当前正在对话的客户全名是「{clean_name}」。称呼时严格遵循以下规则：\n"
                    f"- 「X先生/X女士/X小姐」用客户的姓 → 「{first}先生 / {first}女士」\n"
                    f"- 「X哥/X姐/X总/X爷」用客户名末字 → 「{last}哥 / {last}姐」\n"
                    f"- 直接呼名也用末字「{last}」\n"
                    "禁止使用示例里的其他人名（那是过往客户）。"
                )
            else:
                # 英文/拼音名：直接用完整名字，绝不要拆字加哥/姐（会得到 "a哥" 这种荒谬称呼）
                extras.append(
                    f"当前正在对话的客户名是「{clean_name}」（英文/拼音名）。称呼时严格遵循：\n"
                    f"- 直接喊「{clean_name}」即可，比如「{clean_name}，最近怎么样」「好嘞{clean_name}」\n"
                    f"- 绝对禁止拆字加'哥/姐'：不能用「{clean_name[-1]}哥」「{clean_name[0]}先生」等\n"
                    f"- 也可以不带名字，直接说「您」「咱们」\n"
                    "禁止使用示例里的其他人名（那是过往中文客户的称呼）。"
                )

    if few_shot:
        extras.append(
            "请参考以下「该客户经理过往真实对话」的风格回复——口吻、长度、emoji 使用习惯都要贴近示例。"
            "注意：示例里的人名（'X哥/X姐'等）只是历史对话客户，不要直接复用，要按上面规则用当前客户的名字。"
        )
        for i, ex in enumerate(few_shot, 1):
            q = (ex.get("q") or "").strip()
            # few-shot 用原始 a（含历史客户名，给 LLM 看完整风格）
            a = (ex.get("a_raw") or ex.get("a") or "").strip()
            if not q or not a:
                continue
            extras.append(f"\n示例 {i}:\n客户：{q}\n客户经理：{a}")

    if not extras:
        return base
    return base + "\n\n" + "\n\n".join(extras)


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

        if provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=msgs_array,
            )
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
        logger.error("LLM 调用失败（provider=%s）：%s", settings.llm_provider, exc)
        return None

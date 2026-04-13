from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── 大模型配置 ──────────────────────────────────────────
    # provider: anthropic / openai / moonshot / zhipu / qwen / custom
    llm_provider: str = "anthropic"
    llm_api_key: str = ""          # 存储 API Key（可以有 Key 但不启用）
    llm_base_url: str = ""         # 自定义 base URL（OpenAI 兼容接口用）
    llm_model: str = ""            # 统一模型名（为空时按 provider 使用默认值）
    llm_enabled: bool = False      # 独立开关：即使配置了 Key 也可关闭 LLM
    system_prompt: str = "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。"

    # ── 向后兼容（旧字段，供已有 .env 文件过渡用） ──────────
    claude_api_key: str = ""
    claude_model: str = "claude-haiku-4-5"

    # ── 废话库 ───────────────────────────────────────────────
    filler_enabled: bool = False   # 无规则命中时从废话库随机抽取

    # ── 回复延迟（随机扰动） ─────────────────────────────────
    reply_delay_min_seconds: float = 1.0
    reply_delay_max_seconds: float = 5.0

    # ── 存储 ─────────────────────────────────────────────────
    database_url: str = "sqlite:///./messages.db"
    log_level: str = "INFO"

    # ── Mac Watcher ──────────────────────────────────────────
    poll_interval_seconds: int = 5
    wecom_bundle_id: str = "com.tencent.WeWorkMac"
    # 静默发送：发送回复时不抢焦点；失败再回退到激活窗口
    silent_send: bool = False

    # ── 排除名单 ─────────────────────────────────────────────
    # 会话名字包含任一关键词（大小写不敏感）将被跳过，不回复。
    # 在 .env 里用英文逗号分隔：EXCLUDED_SENDERS=经营线索,客户联系,邮件提醒
    excluded_senders: str = "经营线索,客户联系,邮件提醒,企业微信团队,文件传输助手,明珠智企"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def excluded_sender_list(self) -> list[str]:
        """解析 excluded_senders 为列表，空项和空白自动去除。"""
        return [
            s.strip() for s in self.excluded_senders.split(",") if s.strip()
        ]

    def is_sender_excluded(self, sender: str) -> bool:
        """判断 sender_id 是否命中任一排除关键词（子串匹配，大小写不敏感）。"""
        if not sender:
            return False
        low = sender.lower()
        return any(kw.lower() in low for kw in self.excluded_sender_list)

    @property
    def effective_api_key(self) -> str:
        """优先使用新字段 llm_api_key，向后兼容旧字段 claude_api_key。"""
        return self.llm_api_key or self.claude_api_key

    @property
    def effective_model(self) -> str:
        """优先使用新字段 llm_model，为空时按 provider 返回默认模型名。"""
        if self.llm_model:
            return self.llm_model
        defaults = {
            "anthropic": "claude-haiku-4-5",
            "openai": "gpt-4o-mini",
            "moonshot": "moonshot-v1-8k",
            "zhipu": "glm-4-flash",
            "qwen": "qwen-turbo",
            "custom": "",
        }
        return defaults.get(self.llm_provider, self.claude_model)


settings = Settings()

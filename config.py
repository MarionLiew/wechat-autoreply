import re
from datetime import datetime, time
from typing import Optional

from pydantic_settings import BaseSettings


def _parse_work_hours(raw: str) -> list[tuple[time, time]]:
    """解析工作时间字符串为 (start, end) 列表。

    格式："08:30-12:01,14:00-17:31"
    空字符串 → 空列表（表示 24 小时）。
    """
    if not raw or not raw.strip():
        return []
    slots = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", part)
        if not m:
            raise ValueError(f"工作时间格式错误：{part!r}，应为 HH:MM-HH:MM")
        slots.append((
            time.fromisoformat(m.group(1)),
            time.fromisoformat(m.group(2)),
        ))
    return slots


def _parse_work_days(raw: str) -> set[int]:
    """解析工作日字符串为星期几集合。

    格式："0,1,2,3,4"（0=周一, 6=周日）
    空字符串 → 空集合（表示每天）。
    """
    if not raw or not raw.strip():
        return set()
    return {int(d.strip()) for d in raw.split(",") if d.strip()}


class Settings(BaseSettings):
    # ── 大模型配置 ──────────────────────────────────────────
    # provider: anthropic / openai / moonshot / zhipu / qwen / mimo / custom
    llm_provider: str = "anthropic"
    llm_api_key: str = ""          # 存储 API Key（可以有 Key 但不启用）
    llm_base_url: str = ""         # 自定义 base URL（OpenAI 兼容接口用）
    llm_model: str = ""            # 统一模型名（为空时按 provider 使用默认值）
    llm_enabled: bool = False      # 独立开关：即使配置了 Key 也可关闭 LLM
    system_prompt: str = "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。"

    # ── 各 provider 独立 API Key（切换 provider 时自动带出） ──
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    moonshot_api_key: str = ""
    zhipu_api_key: str = ""
    qwen_api_key: str = ""
    mimo_api_key: str = ""

    # ── 第三层兜底：OpenRouter（mimo、moonshot 都失败后再试） ──
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash"

    # ── 学习飞轮：被动捕获真人客户经理的纠正/补答为 QA 候选 ──
    # 开启后，bot 读会话面板时顺手采集"真人手打、非 bot 发"的回复入 learned_qa.jsonl，
    # 供后续评分/审核 → 增量嵌入进 RAG。纯采集，不影响回复逻辑。
    learn_capture_enabled: bool = True

    # ── 向后兼容（旧字段，供已有 .env 文件过渡用） ──────────
    claude_api_key: str = ""
    claude_model: str = "claude-haiku-4-5"

    # ── 废话库 ───────────────────────────────────────────────
    filler_enabled: bool = False   # 无规则命中时从废话库随机抽取

    # ── LLM 全部失败时的保底话术 ─────────────────────────────
    # 当 mimo 与 moonshot 兜底都连不上时，不再静默跳过，而是发这句安抚话术，
    # 让客户知道消息已收到、有人会跟进。设为空字符串则恢复旧的静默行为。
    llm_fallback_reply: str = "稍等一下哈，马上回复您～"

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

    # 经营线索定时主动扫描间隔（tick 数），默认 120 tick ≈ 10 分钟
    # 设为 0 则禁用定时扫描，只在经营线索有未读消息时处理
    leads_proactive_interval: int = 120

    # 群聊自动回复：默认关闭，避免群里被 @ 时给所有人刷屏
    group_chat_reply: bool = False

    # 只回复真实外部微信客户（会话带 "@微信" 标记）。
    # 企微里群聊、在线客服/智能客服、经营线索、系统通知、内部联系人等都没有这个标记，
    # 开启后这些在"点开会话"之前就被跳过，彻底不抢焦点、不误回。默认开启。
    require_wechat_tag: bool = True

    # LLM 速率限制：同一客户每分钟最多调用 N 次，超出改走 filler 或跳过
    llm_rate_limit_per_minute: int = 6

    # 给 LLM 的上下文消息数（触发 LLM 时会尝试读最近 N 条作为上下文）
    context_message_count: int = 3

    # 废话库防重复：同一客户最近 N 次回复不会选中同一句 filler
    filler_antirepeat_window: int = 5

    # 自回环防护时长（秒）：我方刚发过的同一文本，在此窗口内若被 AX 读到视为自己的消息。
    # 超过此窗口后，对方若真发相同文本会被正常处理。
    echo_protect_seconds: float = 120.0

    # ── 企微内置 AI 面板回复（实验性） ─────────────────────────
    # 开启后：rules 命中失败时，优先用企微自带 AI 助手(热键唤出面板，
    # 附加当前客户完整聊天记录为 context，令其自己读)生成回复；
    # 面板交互失败（找不到控件/超时）才回退到下面的 RAG/LLM 链路。
    wecom_ai_enabled: bool = False
    wecom_ai_timeout_seconds: float = 30.0

    # ── RAG（按客户经理蒸馏的话术库） ─────────────────────────
    # 总开关；为 True 时 engine 在 rules 之后插入 RAG 层
    rag_enabled: bool = False
    # 用哪位客户经理的话术风格回复（必须存在 data/rag_index/<rag_manager>/）
    rag_manager: str = "罗响"
    # top-1 score ≥ 此阈值 → 直接复用历史回复（A 路径）
    rag_direct_threshold: float = 0.85
    # top-1 score ≥ 此阈值 → 走 few-shot 增强 LLM（B 路径）；低于则跳过 RAG
    rag_fewshot_threshold: float = 0.55
    # few-shot 示例条数
    rag_topk: int = 3

    # ── 排除名单 ─────────────────────────────────────────────
    # 会话名字包含任一关键词（大小写不敏感）将被跳过，不回复。
    # 在 .env 里用英文逗号分隔：EXCLUDED_SENDERS=经营线索,客户联系,邮件提醒
    excluded_senders: str = "经营线索,客户联系,邮件提醒,企业微信团队,文件传输助手,明珠智企"

    # ── 工作时间（仅在此时间段内自动回复） ───────────────────
    # 格式：HH:MM 24 小时制。支持多个时段（上午/下午），用逗号分隔。
    # 例："08:30-12:01,14:00-17:31" 表示上午 8:30~12:01、下午 14:00~17:31。
    # 设为空字符串则 24 小时回复。
    work_hours: str = "08:30-12:01,14:00-17:31"
    # 工作日：0=周一 ... 6=周日。设为空字符��则每天。
    work_days: str = "0,1,2,3,4"

    # ── 每日邮件报表 ─────────────────────────────────────────
    # 每日 0 点把"昨日"汇总（回复/失败/线索条数）通过 SMTP 发到目标邮箱
    daily_report_enabled: bool = False  # 总开关
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465                # QQ 用 SSL
    smtp_user: str = ""                 # 发件邮箱（如 xxx@qq.com）
    smtp_password: str = ""             # QQ 的 SMTP 授权码（不是登录密码）
    smtp_from: str = ""                 # 显示发件人；为空默认用 smtp_user
    smtp_to: str = ""                   # 收件邮箱

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
    def provider_api_keys(self) -> dict[str, str]:
        """各 provider 独立 key 的映射。"""
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "moonshot": self.moonshot_api_key,
            "zhipu": self.zhipu_api_key,
            "qwen": self.qwen_api_key,
            "mimo": self.mimo_api_key,
        }

    @property
    def effective_api_key(self) -> str:
        """优先使用当前 provider 的独立 key，其次 llm_api_key，最后 claude_api_key。"""
        provider_key = self.provider_api_keys.get(self.llm_provider, "")
        return provider_key or self.llm_api_key or self.claude_api_key

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
            "mimo": "mimo-v2.5",
            "custom": "",
        }
        return defaults.get(self.llm_provider, self.claude_model)

    def is_work_time(self, now: Optional[datetime] = None) -> bool:
        """判断当前是否在工作时间（工作日 + 工作时段）内。

        工作时间配置为空字符串时视为 24 小时 / 每天，始终返回 True。
        """
        if now is None:
            now = datetime.now()
        # 工作日检查
        work_days = _parse_work_days(self.work_days)
        if work_days and now.weekday() not in work_days:
            return False
        # 时段检查
        slots = _parse_work_hours(self.work_hours)
        if not slots:
            return True  # 未配置 = 24 小时
        current = now.time()
        return any(start <= current <= end for start, end in slots)


settings = Settings()


def validate_startup_config() -> list[str]:
    """启动期配置校验，返回 warning 列表（空 = 一切正常）。

    严重错误（阻止启动）应抛 RuntimeError；非致命问题打 warning 让 daemon 继续跑。
    """
    import logging
    from pathlib import Path
    log = logging.getLogger("config.validate")
    warns: list[str] = []

    # LLM
    if settings.llm_enabled:
        if not settings.effective_api_key:
            raise RuntimeError("LLM_ENABLED=true 但 LLM_API_KEY 为空，请在 .env 配置")
        valid_providers = {"anthropic", "openai", "moonshot", "zhipu", "qwen", "mimo", "custom"}
        if settings.llm_provider not in valid_providers:
            raise RuntimeError(
                f"LLM_PROVIDER={settings.llm_provider!r} 不支持，仅支持 {valid_providers}"
            )
        if settings.llm_provider == "custom" and not settings.llm_base_url:
            raise RuntimeError("LLM_PROVIDER=custom 时必须设 LLM_BASE_URL")

    # RAG
    if settings.rag_enabled:
        if not settings.rag_manager:
            raise RuntimeError("RAG_ENABLED=true 但 RAG_MANAGER 为空")
        idx = Path(__file__).resolve().parent / "data" / "rag_index" / settings.rag_manager
        emb = idx / "embeddings.npy"
        meta = idx / "metadata.jsonl"
        if not emb.exists() or not meta.exists():
            raise RuntimeError(
                f"RAG_ENABLED=true 但索引文件不存在：{emb} / {meta}。"
                "请先运行 scripts/build_rag_index.py"
            )

    # 邮件
    if settings.daily_report_enabled:
        if not settings.smtp_user or not settings.smtp_password or not settings.smtp_to:
            warns.append(
                "DAILY_REPORT_ENABLED=true 但 SMTP_USER/SMTP_PASSWORD/SMTP_TO 任一为空，"
                "邮件无法发出"
            )

    # WeCom bundle id 可解析（osascript 试一下）
    try:
        import subprocess
        r = subprocess.run(
            ["osascript", "-e", f'id of app id "{settings.wecom_bundle_id}"'],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            warns.append(
                f"WECOM_BUNDLE_ID={settings.wecom_bundle_id} 系统找不到对应 App，"
                "daemon 启动后会报 RuntimeError。请确认企业微信已安装。"
            )
    except Exception as exc:
        warns.append(f"WeCom bundle id 校验失败：{exc}")

    for w in warns:
        log.warning("⚠ %s", w)
    return warns

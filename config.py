from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude AI
    claude_api_key: str
    claude_model: str = "claude-haiku-4-5"
    system_prompt: str = "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。"

    # Storage
    database_url: str = "sqlite:///./messages.db"
    log_level: str = "INFO"

    # Mac Watcher
    poll_interval_seconds: int = 5
    wecom_bundle_id: str = "com.tencent.WeWorkMac"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

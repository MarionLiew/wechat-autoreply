"""
初始化向导辅助函数：配置检测、验证、读写、进程管理。

所有函数均为纯函数或轻量 I/O，不依赖 Streamlit，便于测试。
"""

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

_ROOT = Path(__file__).parent.parent
ENV_PATH = _ROOT / ".env"
ENV_EXAMPLE_PATH = _ROOT / ".env.example"
RULES_FILE = _ROOT / "rules.json"
FILLERS_FILE = _ROOT / "fillers.json"

# provider → 默认模型名
PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "moonshot": "moonshot-v1-8k",
    "zhipu": "glm-4-flash",
    "qwen": "qwen-turbo",
    "custom": "",
}

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI",
    "moonshot": "月之暗面 (Moonshot)",
    "zhipu": "智谱 AI (GLM)",
    "qwen": "阿里百炼 (Qwen)",
    "custom": "自定义（OpenAI 兼容）",
}


# ── 配置状态检测 ───────────────────────────────────────────

def is_configured() -> bool:
    """
    判断是否已完成初始化：.env 存在，且至少通过向导走过一次
    （以 WIZARD_DONE=true 标志位为准）。
    """
    if not ENV_PATH.exists():
        return False
    vals = dotenv_values(ENV_PATH)
    return vals.get("WIZARD_DONE", "").lower() == "true"


def read_current_config() -> dict:
    """读取 .env 当前值，不存在的 key 返回默认值。"""
    vals = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return {
        "LLM_PROVIDER": vals.get("LLM_PROVIDER", "anthropic"),
        "LLM_API_KEY": vals.get("LLM_API_KEY", "") or vals.get("CLAUDE_API_KEY", ""),
        "LLM_BASE_URL": vals.get("LLM_BASE_URL", ""),
        "LLM_MODEL": vals.get("LLM_MODEL", ""),
        "LLM_ENABLED": vals.get("LLM_ENABLED", "false").lower() == "true",
        "SYSTEM_PROMPT": vals.get("SYSTEM_PROMPT", "你是一位专业的客服助手，请用简洁、礼貌的中文回复客户问题。"),
        "FILLER_ENABLED": vals.get("FILLER_ENABLED", "false").lower() == "true",
        "REPLY_DELAY_MIN_SECONDS": float(vals.get("REPLY_DELAY_MIN_SECONDS", "1.0")),
        "REPLY_DELAY_MAX_SECONDS": float(vals.get("REPLY_DELAY_MAX_SECONDS", "5.0")),
        "POLL_INTERVAL_SECONDS": int(vals.get("POLL_INTERVAL_SECONDS", "5")),
        "LOG_LEVEL": vals.get("LOG_LEVEL", "INFO"),
        "DATABASE_URL": vals.get("DATABASE_URL", "sqlite:///./messages.db"),
        "WECOM_BUNDLE_ID": vals.get("WECOM_BUNDLE_ID", "com.tencent.WeWorkMac"),
    }


# ── API Key 验证 ───────────────────────────────────────────

def validate_api_key(provider: str, api_key: str, base_url: str = "") -> tuple[bool, str]:
    """
    用给定参数构建临时客户端，发送极短消息验证 Key 有效性。
    不走全局 settings 单例，避免污染守护进程状态。
    """
    if not api_key.strip():
        return False, "API Key 不能为空"

    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=5,
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            import openai
            _PROVIDER_BASE_URLS = {
                "openai": None,
                "moonshot": "https://api.moonshot.cn/v1",
                "zhipu": "https://open.bigmodel.cn/api/paas/v4",
                "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "custom": base_url or None,
            }
            resolved_url = base_url or _PROVIDER_BASE_URLS.get(provider)
            kwargs = {"api_key": api_key}
            if resolved_url:
                kwargs["base_url"] = resolved_url
            client = openai.OpenAI(**kwargs)
            model = PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")
            client.chat.completions.create(
                model=model,
                max_tokens=5,
                messages=[{"role": "user", "content": "hi"}],
            )
        return True, "API Key 验证成功"

    except Exception as e:
        err = str(e)
        if "auth" in err.lower() or "401" in err or "invalid" in err.lower():
            return False, f"API Key 无效：{err}"
        if "connect" in err.lower() or "network" in err.lower() or "timeout" in err.lower():
            return False, f"网络连接失败：{err}"
        return False, f"验证失败：{err}"


# ── 系统权限检测 ───────────────────────────────────────────

def check_accessibility_permission() -> tuple[bool, str]:
    """
    检测 macOS Accessibility 权限。
    用 atomacos 探针；未安装时降级为假定已授权。
    """
    try:
        import atomacos
        atomacos.NativeUIElement.getFrontmostApp()
        return True, "辅助功能权限已授予"
    except ImportError:
        return True, "atomacos 未安装，跳过权限检测"
    except Exception as e:
        err = str(e).lower()
        if any(kw in err for kw in ("not trusted", "axerror", "disabled", "not enabled")):
            return False, "辅助功能权限未授予"
        # 其他错误（如企业微信未启动）不代表无权限
        return True, "辅助功能权限已授予（权限检测遇到非权限错误，已忽略）"


def check_wecom_running(bundle_id: str = "com.tencent.WeWorkMac") -> tuple[bool, str]:
    """检查企业微信是否在运行（用 psutil 直接读取进程列表，不依赖 subprocess）。"""
    try:
        import psutil
        keywords = ("WeWorkMac", "WXWork", "企业微信")
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = proc.info.get("name") or ""
                exe = proc.info.get("exe") or ""
                cmdline = " ".join(proc.info.get("cmdline") or [])
                combined = f"{name} {exe} {cmdline}"
                if any(kw in combined for kw in keywords):
                    return True, "企业微信正在运行"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    return False, "未检测到企业微信，请先启动"


# ── 写入配置 ───────────────────────────────────────────────

def write_env(config: dict) -> None:
    """
    将配置项逐条写入 .env。
    文件不存在时先从 .env.example 复制骨架。
    """
    if not ENV_PATH.exists():
        if ENV_EXAMPLE_PATH.exists():
            shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)
        else:
            ENV_PATH.touch()

    for key, value in config.items():
        set_key(str(ENV_PATH), key, str(value))


def set_env_key(key: str, value: str) -> None:
    """单独更新 .env 中的某一个 key（用于快捷开关）。"""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(str(ENV_PATH), key, value)


def ensure_rules_file(initial_rules: list[dict] | None = None) -> None:
    """rules.json 不存在时写入初始内容（或空骨架）。"""
    if RULES_FILE.exists():
        return
    data = {"rules": initial_rules or []}
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_fillers_file(initial_fillers: list[str] | None = None) -> None:
    """fillers.json 不存在时写入初始内容（或空骨架）。"""
    if FILLERS_FILE.exists():
        return
    data = {"fillers": initial_fillers or []}
    with open(FILLERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 守护进程管理 ───────────────────────────────────────────────

_PID_FILE = _ROOT / ".daemon.pid"
_LOG_FILE = _ROOT / "daemon.log"


def start_daemon() -> tuple[bool, str]:
    """以后台子进程启动 run.py，将 PID 写入 .daemon.pid。"""
    # 若已在运行则直接返回
    running, msg = get_daemon_status()
    if running:
        return True, msg

    python = sys.executable  # 使用当前 venv 的 Python
    log_fh = open(_LOG_FILE, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [python, str(_ROOT / "run.py")],
            cwd=str(_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # 与 Streamlit 进程组分离，避免一起被关掉
        )
        _PID_FILE.write_text(str(proc.pid))
        return True, f"守护进程已启动（PID: {proc.pid}）"
    except Exception as e:
        return False, f"启动失败：{e}"


def stop_daemon() -> tuple[bool, str]:
    """向守护进程发送 SIGTERM 并清理 PID 文件。"""
    if not _PID_FILE.exists():
        return False, "守护进程未在运行"
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        _PID_FILE.unlink(missing_ok=True)
        return True, f"守护进程已停止（PID: {pid}）"
    except ProcessLookupError:
        _PID_FILE.unlink(missing_ok=True)
        return False, "进程已不存在，PID 文件已清理"
    except Exception as e:
        return False, f"停止失败：{e}"


def get_daemon_status() -> tuple[bool, str]:
    """检查守护进程是否在运行。"""
    if not _PID_FILE.exists():
        return False, "未运行"
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = 只检测进程是否存在
        return True, f"运行中（PID: {pid}）"
    except ProcessLookupError:
        _PID_FILE.unlink(missing_ok=True)
        return False, "已停止"
    except PermissionError:
        return True, f"运行中（PID: {int(_PID_FILE.read_text().strip())}）"
    except Exception:
        return False, "状态未知"

"""
启动入口：企业微信 Mac 桌面端自动回复守护进程

用法：
    python run.py

前置条件：
    1. macOS + 系统设置 → 辅助功能 → 勾选 Terminal（或 Python）
    2. 企业微信已登录并保持运行
    3. .env 文件已配置 LLM_API_KEY 等

管理界面（可选，另开终端）：
    streamlit run admin/app.py --server.port 8501
"""

import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import settings
from wecom.mac_watcher import WeChatWatcher

# ── 日志配置：文件轮转 + 控制台 ────────────────────────────────────
_LOG_PATH = ROOT / "daemon.log"
_log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = RotatingFileHandler(
    _LOG_PATH, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_file_handler.setFormatter(_fmt)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

_root = logging.getLogger()
_root.setLevel(_log_level)
# 清空已有 handler（避免 basicConfig 双倍输出）
for h in list(_root.handlers):
    _root.removeHandler(h)
_root.addHandler(_file_handler)
_root.addHandler(_console_handler)


def _install_signal_handlers(watcher: WeChatWatcher, logger: logging.Logger) -> None:
    """安装 SIGTERM/SIGINT 处理器：触发优雅退出（不打断正在进行的 tick）。"""
    def _handler(signum, frame):
        name = {2: "SIGINT", 15: "SIGTERM"}.get(signum, f"signal-{signum}")
        logger.warning("收到 %s，下个 tick 结束后优雅退出", name)
        watcher.request_stop()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


if __name__ == "__main__":
    import time
    logger = logging.getLogger("run")

    # 崩溃自拉起：捕获任何未处理异常后等几秒重启
    backoff = 3
    while True:
        try:
            watcher = WeChatWatcher()
            _install_signal_handlers(watcher, logger)
            watcher.run()
            if watcher._should_stop:
                logger.info("daemon 主动优雅退出，不再重启")
                break
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，退出")
            break
        except Exception as exc:
            logger.exception("守护进程异常退出，%d 秒后重启：%s", backoff, exc)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)  # 指数退避，上限 60 秒
            continue
        # 正常退出（watcher.run 的 while 跑完）也重启，避免静默停摆
        logger.warning("watcher.run() 正常返回，异常情况，1 秒后重启")
        time.sleep(1)

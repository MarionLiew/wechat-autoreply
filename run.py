"""
启动入口：企业微信 Mac 桌面端自动回复守护进程

用法：
    python run.py

前置条件：
    1. macOS + 系统设置 → 辅助功能 → 勾选 Terminal（或 Python）
    2. 企业微信已登录并保持运行
    3. .env 文件已配置 CLAUDE_API_KEY 等

管理界面（可选，另开终端）：
    streamlit run admin/app.py --server.port 8501
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中（直接 python run.py 时有效）
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from wecom.mac_watcher import WeChatWatcher

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    import time
    logger = logging.getLogger("run")

    # 崩溃自拉起：捕获任何未处理异常后等几秒重启
    backoff = 3
    while True:
        try:
            watcher = WeChatWatcher()
            watcher.run()
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

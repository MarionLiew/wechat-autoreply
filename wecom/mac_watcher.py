"""
企业微信 Mac 桌面端自动回复监听器

使用 macOS Accessibility API（atomacos）轮询企业微信客户端，
检测未读消息并触发自动回复。

前置步骤
--------
1. 系统设置 → 隐私与安全性 → 辅助功能 → 勾选 Terminal（或 Python）
2. 用 Accessibility Inspector 确认真实的 AX 元素选择器：
   Xcode → Open Developer Tool → Accessibility Inspector
   将鼠标悬停在企业微信各区域，记录 AXRole / AXIdentifier
3. 按实际情况更新本文件中所有标注 TODO(selector) 的位置

Bundle ID 查询：
   osascript -e 'id of app "企业微信"'
"""

import hashlib
import logging
import time
from typing import Optional

try:
    import atomacos
except ImportError:
    raise ImportError(
        "atomacos 未安装。请运行：pip install atomacos pyobjc-core\n"
        "注意：atomacos 仅支持 macOS。"
    )

from config import settings
from reply import engine
from storage import message_log

logger = logging.getLogger(__name__)


class WeChatWatcher:
    """监听企业微信 Mac 客户端的未读消息并自动回复。"""

    def __init__(self) -> None:
        self._app = None
        # 从数据库加载近 24h 已处理的消息哈希，防止重启后重复回复
        self._processed: set[str] = message_log.get_recent_hashes(hours=24)

    # ------------------------------------------------------------------
    # App / Window helpers
    # ------------------------------------------------------------------

    def _get_app(self):
        """获取企业微信 App 引用，不在运行时抛出 RuntimeError。"""
        if self._app is None:
            try:
                self._app = atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
            except Exception as exc:
                raise RuntimeError(
                    f"找不到企业微信（bundle_id={settings.wecom_bundle_id}），"
                    "请确认 App 已启动。"
                ) from exc
        return self._app

    def _get_main_window(self):
        """返回企业微信主窗口。"""
        app = self._get_app()
        try:
            windows = app.AXWindows
        except Exception as exc:
            raise RuntimeError("无法枚举企业微信窗口") from exc
        if not windows:
            raise RuntimeError("企业微信没有已打开的窗口")
        return windows[0]

    # ------------------------------------------------------------------
    # Unread conversation detection
    # ------------------------------------------------------------------

    def _find_conversation_list(self, window):
        """
        定位会话列表容器。

        TODO(selector): 用 Accessibility Inspector 确认
        - 典型结构：AXSplitGroup > AXScrollArea（第一个，即左侧栏）> AXList/AXTable
        - 或直接找 AXIdentifier="conversation_list" 的 AXScrollArea
        尝试顺序：先精确 ID，再按结构猜测。
        """
        # 尝试 1：通过 Identifier 精确定位
        for ident in ("conversation_list", "ConversationList", "sessionList"):
            try:
                return window.findFirst(AXRole="AXScrollArea", AXIdentifier=ident)
            except Exception:
                pass

        # 尝试 2：取第一个 AXScrollArea（通常是左侧会话栏）
        try:
            areas = window.findAll(AXRole="AXScrollArea")
            if areas:
                return areas[0]
        except Exception:
            pass

        # 尝试 3：AXList / AXTable 直接挂在 window 下
        for role in ("AXList", "AXTable", "AXOutline"):
            try:
                return window.findFirst(AXRole=role)
            except Exception:
                pass

        raise RuntimeError(
            "找不到会话列表容器，请用 Accessibility Inspector 确认选择器后修改 "
            "_find_conversation_list()。"
        )

    def _get_conversation_rows(self, conv_list) -> list:
        """返回会话列表中所有行元素。"""
        for role in ("AXCell", "AXRow", "AXGroup"):
            try:
                rows = conv_list.findAll(AXRole=role)
                if rows:
                    return rows
            except Exception:
                pass
        return []

    def _has_unread_badge(self, row) -> bool:
        """
        判断某个会话行是否有未读标记。

        TODO(selector): 常见的未读角标实现方式：
        1. AXValueIndicator（数字角标，AXValue 为整数）
        2. 子 AXStaticText，其 AXValue 为纯数字字符串
        3. 子元素的 AXDescription 含"未读"
        4. 子元素的 AXTitle 含"未读"
        遇到无法识别的情况，打开 DEBUG 日志可看到元素结构。
        """
        try:
            badge = row.findFirst(AXRole="AXValueIndicator")
            val = badge.AXValue
            if isinstance(val, (int, float)) and val > 0:
                return True
            if isinstance(val, str) and val.isdigit() and int(val) > 0:
                return True
        except Exception:
            pass

        try:
            for child in _safe_children(row):
                # 检查数字角标文本
                ax_val = str(getattr(child, "AXValue", "") or "")
                if ax_val.isdigit() and int(ax_val) > 0:
                    return True
                # 检查描述文字
                for attr in ("AXDescription", "AXTitle", "AXHelp"):
                    text = str(getattr(child, attr, "") or "")
                    if "未读" in text:
                        return True
        except Exception:
            pass

        return False

    def find_unread_conversations(self) -> list:
        """返回含未读消息的会话行元素列表。"""
        try:
            window = self._get_main_window()
            conv_list = self._find_conversation_list(window)
            rows = self._get_conversation_rows(conv_list)
        except Exception as exc:
            logger.warning("查找未读会话失败：%s", exc)
            return []

        unread = [r for r in rows if self._has_unread_badge(r)]
        logger.debug("共 %d 个会话，其中 %d 个未读", len(rows), len(unread))
        return unread

    # ------------------------------------------------------------------
    # Message extraction
    # ------------------------------------------------------------------

    def _find_chat_area(self, window):
        """
        定位聊天消息区域（右侧主面板）。

        TODO(selector): 用 Accessibility Inspector 确认
        - 通常是第二个或最大的 AXScrollArea
        - 或有 AXIdentifier="chat_area" / "messageList" 等
        """
        for ident in ("chat_area", "messageList", "ChatArea", "MessageList"):
            try:
                return window.findFirst(AXRole="AXScrollArea", AXIdentifier=ident)
            except Exception:
                pass

        # 取面积最大的 AXScrollArea 作为消息区
        try:
            areas = window.findAll(AXRole="AXScrollArea")
            if len(areas) >= 2:
                return areas[-1]  # 最后一个通常是消息区
            if areas:
                return areas[0]
        except Exception:
            pass

        raise RuntimeError(
            "找不到聊天消息区域，请用 Accessibility Inspector 确认选择器后修改 "
            "_find_chat_area()。"
        )

    def extract_last_message(self, conv_row) -> Optional[dict]:
        """
        点击会话行，提取最后一条消息的文本与发送方标识。

        返回 dict(text, sender_id, msg_hash)，失败返回 None。
        """
        # 获取会话标题作为 sender_id
        sender_id = str(getattr(conv_row, "AXTitle", "") or "").strip()
        if not sender_id:
            sender_id = str(getattr(conv_row, "AXLabel", "") or "unknown").strip()

        # 点击进入会话
        try:
            conv_row.Press()
            time.sleep(0.4)  # 等待聊天区刷新
        except Exception as exc:
            logger.error("点击会话失败（%s）：%s", sender_id, exc)
            return None

        try:
            window = self._get_main_window()
            chat_area = self._find_chat_area(window)
        except Exception as exc:
            logger.warning("找不到聊天区域：%s", exc)
            return None

        # 取聊天区内所有静态文本，最后一条即最新消息
        try:
            # TODO(selector): 消息文本可能是 AXStaticText 或 AXTextField（只读）
            texts = chat_area.findAll(AXRole="AXStaticText")
            if not texts:
                texts = chat_area.findAll(AXRole="AXTextField")

            # 过滤空文本和时间戳（纯数字/冒号组成的短字符串）
            candidate_texts = [
                str(el.AXValue or "").strip()
                for el in texts
                if _is_message_text(str(el.AXValue or "").strip())
            ]

            if not candidate_texts:
                logger.debug("会话 %s 未找到有效消息文本", sender_id)
                return None

            text = candidate_texts[-1]
        except Exception as exc:
            logger.error("提取消息文本失败：%s", exc)
            return None

        # 用 sender_id + text 生成稳定哈希，用于去重
        msg_hash = hashlib.sha256(f"{sender_id}:{text}".encode()).hexdigest()

        logger.debug("提取消息 [%s] hash=%s: %s", sender_id, msg_hash[:12], text[:80])
        return {"text": text, "sender_id": sender_id, "msg_hash": msg_hash}

    # ------------------------------------------------------------------
    # Reply sending
    # ------------------------------------------------------------------

    def send_reply(self, reply_text: str) -> bool:
        """
        将回复文本写入输入框并发送。

        TODO(selector): 用 Accessibility Inspector 找到输入框的准确选择器。
        企业微信输入框通常是 AXTextArea 或富文本区域（AXWebArea 内部）。
        """
        try:
            window = self._get_main_window()
        except Exception as exc:
            logger.error("获取主窗口失败：%s", exc)
            return False

        input_box = None

        # 尝试通过 Identifier 精确定位
        for ident in ("chat_input", "messageInput", "ChatInput", "inputArea"):
            try:
                input_box = window.findFirst(AXRole="AXTextArea", AXIdentifier=ident)
                break
            except Exception:
                pass

        # 回退：取所有 AXTextArea，选最后一个（通常是输入框）
        if input_box is None:
            try:
                areas = window.findAll(AXRole="AXTextArea")
                if areas:
                    input_box = areas[-1]
            except Exception:
                pass

        if input_box is None:
            logger.error("找不到输入框，请用 Accessibility Inspector 确认选择器")
            return False

        try:
            input_box.Press()           # 聚焦
            time.sleep(0.1)
            input_box.setString(reply_text)
            time.sleep(0.1)
            input_box.sendKeys("\r")    # 回车发送
            logger.info("已发送回复：%s", reply_text[:60])
            return True
        except Exception as exc:
            logger.error("发送回复失败：%s", exc)
            return False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """单次轮询：检查未读 → 提取 → 回复 → 记录。"""
        try:
            app = self._get_app()
            app.activate()  # 确保窗口在前台，AX 操作更可靠
        except Exception as exc:
            logger.warning("无法激活企业微信：%s", exc)
            self._app = None  # 下次重新获取引用
            return

        for conv in self.find_unread_conversations():
            msg = self.extract_last_message(conv)
            if msg is None:
                continue

            if msg["msg_hash"] in self._processed:
                logger.debug("已处理过，跳过：%s", msg["msg_hash"][:12])
                continue

            logger.info(
                "新消息 [%s]: %s",
                msg["sender_id"],
                msg["text"][:80],
            )

            result = engine.process_message(msg["text"])

            logger.info(
                "回复 [来源=%s]: %s",
                result["source"],
                result["content"][:80],
            )

            sent = self.send_reply(result["content"])
            if sent:
                self._processed.add(msg["msg_hash"])
                message_log.save(
                    msg_hash=msg["msg_hash"],
                    customer_id=msg["sender_id"],
                    message=msg["text"],
                    reply=result["content"],
                    source=result["source"],
                )

    def run(self) -> None:
        """启动轮询守护循环。"""
        logger.info(
            "WeCom Mac Watcher 启动，轮询间隔 %ds",
            settings.poll_interval_seconds,
        )
        message_log.init_db()
        while True:
            try:
                self.tick()
            except Exception as exc:
                logger.error("轮询异常：%s", exc)
            time.sleep(settings.poll_interval_seconds)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _safe_children(element) -> list:
    """安全地获取子元素列表，失败返回空列表。"""
    try:
        return element.AXChildren or []
    except Exception:
        return []


def _is_message_text(text: str) -> bool:
    """
    过滤掉时间戳、空字符串等非消息内容。
    时间戳示例："12:30"、"昨天 18:00"、"2024-01-01"
    """
    if not text or len(text) < 2:
        return False
    # 过滤纯时间/日期格式（简单启发式）
    import re
    if re.fullmatch(r"[\d:年月日/\-\s]+", text):
        return False
    return True

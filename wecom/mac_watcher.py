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
import random
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
        # 记录每个 sender 上一次处理的文本；若该 sender 的未读消失过一次
        # （unread 列表中不再出现），则清除其记录，允许下次同样文本触发新回复。
        self._last_text_by_sender: dict[str, str] = {}

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
        定位会话列表容器（企微 Mac：AXWindow > ... > AXTable）。
        失败时自动触发 ax_tree 快照，方便企微版本升级后重新定位选择器。
        """
        table = _deep_find_first(window, "AXTable", max_depth=10)
        if table is not None:
            return table
        try:
            from wecom import selectors
            selectors._maybe_dump("watcher 未找到会话列表 AXTable")
        except Exception:
            pass
        raise RuntimeError("找不到会话列表 AXTable，请检查企业微信窗口是否正常显示")

    def _get_conversation_rows(self, conv_list) -> list:
        """返回会话列表中所有行元素（AXRow，直接子节点）。"""
        rows = [
            ch for ch in _safe_children(conv_list)
            if str(getattr(ch, "AXRole", "") or "") == "AXRow"
        ]
        return rows

    def _has_unread_badge(self, row) -> bool:
        return self._unread_count(row) > 0

    def _unread_count(self, row) -> int:
        """
        返回某个会话行的未读数（AXButton title 为数字），无则返回 0。
        """
        for btn in _deep_find_all(row, "AXButton", max_depth=5):
            title = str(getattr(btn, "AXTitle", "") or "").strip()
            if title.isdigit():
                n = int(title)
                if n > 0:
                    return n
        return 0

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
        logger.info("会话列表共 %d 行，未读 %d 个", len(rows), len(unread))
        if rows and not unread:
            # 帮助排查：若总会话数>0 但未读=0，可能是 badge 选择器没识别到
            try:
                first_title = str(getattr(rows[0], "AXTitle", "") or "")[:30]
                logger.debug("示例首行标题：%s", first_title)
            except Exception:
                pass
        return unread

    # ------------------------------------------------------------------
    # Message extraction
    # ------------------------------------------------------------------

    def read_last_messages(self, conv_row, count: int) -> list[str]:
        """
        点击会话（静默），从右侧聊天面板读最近 count 条消息文本。

        失败返回空列表；调用方应回退到预览（extract_last_message）。
        """
        if count <= 0:
            return []
        if not _press_conv_row(conv_row):
            return []
        time.sleep(0.35)  # 等聊天区刷新

        try:
            window = self._get_main_window()
        except Exception:
            return []

        # 聊天区通常是最后一个 AXScrollArea 下的 AXWebArea
        scroll_areas = _deep_find_all(window, "AXScrollArea", max_depth=12)
        chat_area = None
        for sa in reversed(scroll_areas):
            web = _deep_find_first(sa, "AXWebArea", max_depth=3)
            if web is not None:
                chat_area = web
                break
        if chat_area is None and scroll_areas:
            chat_area = scroll_areas[-1]
        if chat_area is None:
            return []

        # 取所有 StaticText，倒序扫描取有效文本直到凑够 count 条
        texts = _deep_find_all(chat_area, "AXStaticText", max_depth=15)
        values: list[str] = []
        for t in texts:
            v = str(getattr(t, "AXValue", "") or "").strip()
            if not v:
                continue
            if not _is_message_text(v):
                continue
            if v in ("@微信", "筛选", "搜索", "共", "条", "批量处理"):
                continue
            values.append(v)

        return values[-count:] if values else []

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
        直接从会话行的 AXCell 中读取发送方和最新消息预览，**不点击、不抢焦点**。

        企业微信 Mac 版实测：每个 AXRow > AXCell 内的 AXStaticText 顺序为：
            [0] 发送方名字（如 '刘明瑞(男)-3647'）
            [1] 最新消息预览（如 '你好回我一下'）
            [2..] 时间、'@微信' 等附加标签
        时间/标签过滤由 _is_message_text 完成。

        返回 dict(text, sender_id, msg_hash, conv_row)，失败返回 None。
        """
        texts = _deep_find_all(conv_row, "AXStaticText", max_depth=5)
        values = [
            str(getattr(t, "AXValue", "") or "").strip()
            for t in texts
        ]
        values = [v for v in values if v]

        if len(values) < 2:
            logger.debug("会话行文本不足 2 条：%s", values)
            return None

        sender_id = values[0]
        # 过滤掉时间戳和 '@微信' 这类附加标签，取第一条有效消息
        message_candidates = [
            v for v in values[1:]
            if _is_message_text(v) and v != "@微信"
        ]
        if not message_candidates:
            logger.debug("会话 [%s] 无有效消息文本", sender_id)
            return None

        text = message_candidates[0]
        # hash 含时间戳，保证同人同文本多次发送不会冲突 DB 唯一约束；
        # 去重由 _last_text_by_sender 在内存里处理
        ts = time.time()
        msg_hash = hashlib.sha256(
            f"{sender_id}:{text}:{ts:.3f}".encode()
        ).hexdigest()

        logger.debug("提取消息 [%s] hash=%s: %s", sender_id, msg_hash[:12], text[:80])
        return {
            "text": text,
            "sender_id": sender_id,
            "msg_hash": msg_hash,
            "conv_row": conv_row,
        }

    # ------------------------------------------------------------------
    # Reply sending
    # ------------------------------------------------------------------

    def send_reply(self, reply_text: str, conv_row=None) -> tuple[bool, str]:
        """
        将回复文本写入输入框并发送。

        发送前会点击目标会话行以切换到该会话；会短暂把企业微信拉到前台。
        """
        try:
            app = self._get_app()
            if not settings.silent_send:
                app.activate()
                time.sleep(0.2)
            window = self._get_main_window()
        except Exception as exc:
            logger.error("获取主窗口失败：%s", exc)
            return False, ""

        if conv_row is not None:
            if not _press_conv_row(conv_row):
                logger.error("切换到目标会话失败（所有 Press 策略都不可用）")
                return False, ""
            time.sleep(0.3)

        # 深度遍历找所有 AXTextArea；输入框通常是最后一个（聊天区右下）
        text_areas = _deep_find_all(window, "AXTextArea", max_depth=12)
        # 过滤掉带 AXValue='BOT' 或只读的（如消息列表中的 BOT 标签）
        candidates = []
        for ta in text_areas:
            try:
                val = str(getattr(ta, "AXValue", "") or "")
                # 排除显示为 'BOT' 的标签；真正的输入框 AXValue 一般为空
                if val.strip() == "BOT":
                    continue
                candidates.append(ta)
            except Exception:
                candidates.append(ta)

        if not candidates:
            logger.error("找不到输入框（AXTextArea），共 %d 个候选", len(text_areas))
            return False, ""

        input_box = candidates[-1]
        logger.debug("选用输入框：AXValue=%r", getattr(input_box, "AXValue", ""))

        try:
            try:
                input_box.AXFocused = True
            except Exception:
                pass
            time.sleep(0.1)

            # 写入文本：按顺序尝试多种 API，记录实际用的方法
            used_method = ""
            for name, setter in (
                ("AXValue", lambda: setattr(input_box, "AXValue", reply_text)),
                ("setString", lambda: input_box.setString(string=reply_text)),
                ("sendKeys", lambda: input_box.sendKeys(reply_text)),
            ):
                try:
                    setter()
                    used_method = name
                    break
                except Exception as exc:
                    logger.debug("写入方式 %s 失败：%s", name, exc)

            if not used_method:
                logger.error("无法写入输入框")
                return False, ""

            time.sleep(0.15)
            enter_method = ""

            # ① 元素级 Confirm / AXConfirm（最干净，不需要焦点）
            for act in ("Confirm", "AXConfirm"):
                fn = getattr(input_box, act, None)
                if callable(fn):
                    try:
                        fn()
                        enter_method = act
                        break
                    except Exception as exc:
                        logger.debug("%s 失败：%s", act, exc)

            # ② 元素级 sendKeys 回车（只在非静默模式用；静默时企微不响应此事件）
            if not enter_method and not settings.silent_send:
                try:
                    input_box.sendKeys("\r")
                    enter_method = "sendKeys(\\r)"
                except Exception as exc:
                    logger.debug("元素 sendKeys 回车失败：%s", exc)

            # ③ Quartz 事件：优先定向投递到企微 PID（真正静默），失败才临时激活
            if not enter_method:
                import Quartz
                pid = _get_wecom_pid(settings.wecom_bundle_id)

                if settings.silent_send and pid:
                    # 定向投递：不需要激活窗口
                    try:
                        for down in (True, False):
                            ev = Quartz.CGEventCreateKeyboardEvent(None, 36, down)
                            Quartz.CGEventPostToPid(pid, ev)
                        enter_method = f"Quartz→pid{pid}"
                    except Exception as exc:
                        logger.debug("CGEventPostToPid 失败：%s", exc)

                # 兜底：临时激活 + HIDEventTap + 还原焦点
                if not enter_method:
                    prev_app = None
                    try:
                        from AppKit import NSWorkspace
                        prev_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                    except Exception:
                        pass
                    try:
                        self._get_app().activate()
                        time.sleep(0.15)
                        for down in (True, False):
                            ev = Quartz.CGEventCreateKeyboardEvent(None, 36, down)
                            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                        enter_method = "Quartz(activate)"
                        time.sleep(0.1)
                        if settings.silent_send and prev_app is not None:
                            try:
                                prev_app.activateWithOptions_(0)
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.error("Quartz 回车也失败：%s", exc)
                        return False, ""

            method_label = f"{used_method}+{enter_method}"
            logger.info("已发送回复（方式=%s）：%s", method_label, reply_text[:60])
            return True, method_label
        except Exception as exc:
            logger.error("发送回复失败：%s", exc)
            return False, ""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """单次轮询：检查未读 → 提取 → 回复 → 记录。

        注意：不在轮询阶段抢占前台，避免每 5 秒把企业微信拉到最前。
        只有在需要发送回复时，send_reply() 内部会短暂激活窗口。
        """
        try:
            self._get_app()  # 仅获取引用，不 activate
        except Exception as exc:
            logger.warning("无法获取企业微信 App 引用：%s", exc)
            self._app = None
            return

        unread = self.find_unread_conversations()
        logger.info("本轮检测到 %d 个未读会话", len(unread))

        # 预读所有未读会话的 sender，用于清理"已读过一次"的去重状态
        current_unread_senders: set[str] = set()

        parsed: list[dict] = []
        for conv in unread:
            msg = self.extract_last_message(conv)
            if msg is None:
                continue
            msg["unread_count"] = self._unread_count(conv)
            current_unread_senders.add(msg["sender_id"])
            parsed.append(msg)

        for sender in list(self._last_text_by_sender.keys()):
            if sender not in current_unread_senders:
                self._last_text_by_sender.pop(sender, None)

        for msg in parsed:
            sender = msg["sender_id"]

            if self._last_text_by_sender.get(sender) == msg["text"]:
                logger.debug("同会话重复预览，跳过：%s", sender)
                continue

            # 群聊识别：title 含 "、" 通常表示多人聚合
            is_group = "、" in sender
            if is_group and not settings.group_chat_reply:
                logger.info("群聊已禁用自动回复，跳过 [%s]", sender[:40])
                self._last_text_by_sender[sender] = msg["text"]
                continue

            if settings.is_sender_excluded(sender):
                logger.info("排除名单命中，跳过 [%s]", sender)
                self._last_text_by_sender[sender] = msg["text"]
                continue

            # 读取该会话所有未读消息作为完整上下文
            unread_n = max(1, msg.get("unread_count", 1))
            all_msgs: list[str] = []
            if unread_n >= 2:
                all_msgs = self.read_last_messages(msg["conv_row"], unread_n)
            if not all_msgs:
                all_msgs = [msg["text"]]

            combined_text = "\n".join(all_msgs)
            logger.info(
                "新消息 [%s] 共 %d 条: %s",
                sender, len(all_msgs), combined_text[:120].replace("\n", " | "),
            )

            # 引擎用合并文本做匹配，同时把上下文传给 LLM
            result = engine.process_message(
                combined_text,
                sender_id=sender,
                context=all_msgs,
            )

            if result["source"] == "none":
                logger.info("无匹配规则/废话库/LLM，跳过回复 [%s]: %s",
                            msg["sender_id"], msg["text"][:60])
                # 仅用 hash 标记当前这条消息已处理过，不整体屏蔽 sender
                self._processed.add(msg["msg_hash"])
                continue

            logger.info(
                "回复 [来源=%s]: %s",
                result["source"],
                result["content"][:80],
            )

            delay = random.uniform(
                settings.reply_delay_min_seconds,
                settings.reply_delay_max_seconds,
            )
            logger.debug("随机延迟 %.1f 秒后回复", delay)
            time.sleep(delay)

            t_start = time.monotonic()
            sent, used_method = self.send_reply(
                result["content"], conv_row=msg.get("conv_row")
            )
            latency_ms = int((time.monotonic() - t_start) * 1000)
            if sent:
                self._processed.add(msg["msg_hash"])
                self._last_text_by_sender[sender] = msg["text"]
                message_log.save(
                    msg_hash=msg["msg_hash"],
                    customer_id=sender,
                    message=combined_text,
                    reply=result["content"],
                    source=result["source"],
                    send_method=used_method,
                    latency_ms=latency_ms,
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


def _deep_find_first(root, role: str, max_depth: int = 10):
    """深度优先遍历，返回第一个 AXRole == role 的节点；找不到返回 None。"""
    if max_depth < 0:
        return None
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            return root
    except Exception:
        pass
    for child in _safe_children(root):
        found = _deep_find_first(child, role, max_depth - 1)
        if found is not None:
            return found
    return None


def _get_wecom_pid(bundle_id: str) -> int | None:
    """通过 bundle_id 查找企业微信进程 pid，用于定向投递键盘事件。"""
    try:
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.bundleIdentifier() == bundle_id:
                return int(app.processIdentifier())
    except Exception:
        pass
    return None


def _press_conv_row(row) -> bool:
    """
    点击会话行以切换到该会话。
    尝试顺序：row.Press() → row 下首个 AXCell.Press() → setSelected → 鼠标点击坐标。
    """
    # 1. AXRow 自己（多数情况下不行）
    for target in (row, _deep_find_first(row, "AXCell", max_depth=2)):
        if target is None:
            continue
        for action in ("Press", "AXPress"):
            fn = getattr(target, action, None)
            if callable(fn):
                try:
                    fn()
                    logger.debug("通过 %s.%s 切换会话成功", target.AXRole, action)
                    return True
                except Exception as exc:
                    logger.debug("%s.%s 失败：%s", target.AXRole, action, exc)

    # 2. 尝试设置 selected 属性
    try:
        row.AXSelected = True
        logger.debug("通过 AXSelected=True 切换会话成功")
        return True
    except Exception as exc:
        logger.debug("AXSelected 赋值失败：%s", exc)

    # 3. 鼠标点击行中心坐标
    try:
        pos = row.AXPosition
        size = row.AXSize
        cx = pos.x + size.width / 2
        cy = pos.y + size.height / 2
        from atomacos import _a11y  # noqa
        import Quartz
        event_down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown,
                                                   (cx, cy), Quartz.kCGMouseButtonLeft)
        event_up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp,
                                                 (cx, cy), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)
        logger.debug("通过鼠标点击 (%.0f,%.0f) 切换会话", cx, cy)
        return True
    except Exception as exc:
        logger.debug("鼠标点击失败：%s", exc)

    return False


def _deep_find_all(root, role: str, max_depth: int = 10) -> list:
    """深度优先遍历，返回所有 AXRole == role 的节点。"""
    out: list = []
    if max_depth < 0:
        return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            out.append(root)
    except Exception:
        pass
    for child in _safe_children(root):
        out.extend(_deep_find_all(child, role, max_depth - 1))
    return out


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

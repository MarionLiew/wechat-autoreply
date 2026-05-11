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
        # 每个 sender 最近自动发出的回复 + 时间戳，用于识别"自己发的消息"防自回环；
        # 超过 _echo_window_seconds 的老回复会被清理，对方日后再发同样文本不会被误过滤。
        from collections import deque
        self._recent_replies_by_sender: dict[str, deque[tuple[float, str]]] = {}
        # bot 曾发出的所有回复文本，按 sender 分组（不过期）。
        # 用于 WebArea 被功能浮层遮挡、只能读预览时的补充防回环：
        # 若 preview 文本在此集合里，说明几乎肯定是 bot 自己发的，不应再回。
        # 启动时从 DB 加载近 24h 记录，防止重启后丢失导致自回环。
        self._bot_sent_texts: dict[str, set] = message_log.get_recent_bot_replies(hours=24)
        # 内容级去重：记录每个 sender 上次成功回复时的消息集合（frozenset）。
        # 不会因 sender 暂时离开未读列表而被清除，防止同批次消息被多次回复。
        # 只有当新批次消息集合与上次不同时，才触发回复。
        self._last_replied_batch: dict[str, frozenset] = {}
        # 经营线索：已处理过的线索哈希（旅客文本 hash），防止重复点击同一线索
        self._processed_leads: set[str] = set()

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
        """返回企业微信主窗口（title='企业微信' 的那个，跳过浮动输入法窗口）。"""
        app = self._get_app()
        try:
            windows = app.AXWindows
        except Exception as exc:
            raise RuntimeError("无法枚举企业微信窗口") from exc
        if not windows:
            raise RuntimeError("企业微信没有已打开的窗口")
        # 优先找 title='企业微信' 的窗口，跳过输入法浮层或模态对话框
        for w in windows:
            try:
                title = str(getattr(w, "AXTitle", "") or "")
                if title == "企业微信":
                    return w
            except Exception:
                pass
        # 兜底：取面积最大的窗口
        def _win_area(w):
            try:
                sz = getattr(w, "AXSize", None)
                return (sz[0] * sz[1]) if sz else 0
            except Exception:
                return 0
        return max(windows, key=_win_area)

    # ------------------------------------------------------------------
    # 出方向（我方/系统）模板识别
    # ------------------------------------------------------------------

    @staticmethod
    def _is_outgoing_template(text: str) -> bool:
        """识别"客户经理新加好友的欢迎语 / 系统提示"等出方向模板。

        预览文本无法区分出入方向（与 read_last_messages 用 midx 区分不同），
        而企微在新加好友后系统/客户经理会自动推欢迎语，conv_row 把它显示为
        "未读"预览，bot 若不识别就会去回复自己刚发的开场白，造成连环误发。

        命中关键词组合即视为出方向，跳过。
        """
        if not text:
            return False
        t = text.replace(" ", "")
        # 1. 企微"我已经添加了你"系统消息
        if "我已经添加了你" in t and "可以开始聊天" in t:
            return True
        # 2. 南航客户经理欢迎模板：含"南方航空" + ("客户经理"|"为您提供"|"感谢您选择")
        if "南方航空" in t and (
            "客户经理" in t or "为您提供" in t or "感谢您选择" in t
        ):
            return True
        # 3. 营销/活动模板（生日有礼、🎁 开头活动文案等）
        if t.startswith("🎁") or "生日有礼" in t or "生日赢好礼" in t:
            return True
        return False

    # ------------------------------------------------------------------
    # 当前活跃聊天面板识别 + 会话切换校验
    # ------------------------------------------------------------------

    # 功能浮层 desc 白名单（这些 WebArea 不算"当前聊天客户"）
    # 注意：当浮层完全遮挡聊天面板时，仅靠跳过其 desc 还原不出真正的 sender，
    # 需要在 _switch_to_conv_and_verify 里主动调用 _dismiss_function_panel 折叠浮层。
    _FUNCTION_PANEL_DESCS = {
        "经营大厅", "客户经营专区", "快捷回复", "人工客服", "快速会议",
        "筛选", "批量处理", "搜索",
    }

    def _active_chat_sender(self, window) -> str:
        """返回当前活跃聊天面板的客户名/sender ID。

        策略 A：从 AXWebArea AXDescription 读（普通情况下，desc 即客户名）。
                跳过功能浮层 desc。

        策略 B：当 A 拿不到（AXWebArea 缺失/atomacos 'system memory failure'
                等异常），读 chat header AXStaticText 兜底——企微 chat header
                在 x≈459,y≈266 处显示当前聊天 sender 全称（如 '刘明瑞(男)-3647'），
                这是经营大厅等浮层并存时仍可访问的稳定信号。

        都拿不到返回 ''。
        """
        # 策略 A：AXWebArea desc（浅扫，max_depth=6）
        # 之前 depth=15 在大窗口下要 8s+；改浅扫避免阻塞。WebArea 通常浅层。
        try:
            webs = _deep_find_all(window, "AXWebArea", max_depth=6)
        except Exception:
            webs = []
        for w in webs:
            try:
                desc = str(getattr(w, "AXDescription", "") or "").strip()
            except Exception:
                continue
            if not desc or desc in self._FUNCTION_PANEL_DESCS:
                continue
            return desc

        # 策略 B：chat header StaticText
        # WeCom 主窗口结构稳定：AXWindow → AXSplitGroup → AXSplitGroup → 直接
        # 子节点里有 chat header 的 StaticText（位置 y=50 附近 page title 或
        # y=266 附近 chat banner 内 customer 全称）。
        # 不用 deep_find_all 全树扫（实测 673 个 StaticText × depth=5 也要 9s+），
        # 而是只扫 inner split group 的直接子节点，~10 个节点，<0.1s。
        candidates = []
        try:
            for c in window.AXChildren or []:
                if str(getattr(c, "AXRole", "") or "") != "AXSplitGroup":
                    continue
                for cc in c.AXChildren or []:
                    if str(getattr(cc, "AXRole", "") or "") != "AXSplitGroup":
                        continue
                    # cc = inner split group，扫直接子节点
                    for el in cc.AXChildren or []:
                        try:
                            if str(getattr(el, "AXRole", "") or "") != "AXStaticText":
                                continue
                            pos = getattr(el, "AXPosition", None)
                            val = str(getattr(el, "AXValue", "") or "").strip()
                            if not pos or not val:
                                continue
                            candidates.append((pos[1], pos[0], val))
                        except Exception:
                            continue
        except Exception:
            pass
        # 取 y 最小的（顶部 chat header），并排除噪声
        candidates.sort()
        for y, x, val in candidates:
            if val in ("@微信", "经营线索", "客户联系") or val.startswith(("4月", "5月", "6月")):
                continue
            return val
        return ""

    def _try_dismiss_covering_panel(self, window) -> bool:
        """尝试关闭遮挡聊天面板的功能浮层（如 '客户经营专区'、'经营大厅'）。

        按优先级尝试：
        1. 浮层 WebArea 子树里 AXSubrole='AXCloseButton' 的按钮
        2. 浮层 WebArea 子树里 title 含 '关闭'/'收起'/'返回'/'退出'/'×' 的按钮/链接
        3. 浮层 WebArea 父节点附近的 AXCloseButton（标题栏关闭按钮）
        4. 现有 _dismiss_function_panel（AXCheckBox title='展开'）
        5. 兜底：发 ESC 键给企微 pid（Mac 上多数 popup 接受 ESC 关闭）
        """
        # 先找出当前所有功能浮层 WebArea
        panel_webs = []
        try:
            for w in _deep_find_all(window, "AXWebArea", max_depth=15):
                try:
                    desc = str(getattr(w, "AXDescription", "") or "").strip()
                except Exception:
                    continue
                if desc in self._FUNCTION_PANEL_DESCS:
                    panel_webs.append((desc, w))
        except Exception:
            pass

        def _press(node) -> bool:
            for action in ("Press", "AXPress"):
                fn = getattr(node, action, None)
                if callable(fn):
                    try:
                        fn()
                        return True
                    except Exception:
                        continue
            return False

        # 1+2: 浮层 WebArea 子树里找关闭控件
        for desc, web in panel_webs:
            for role in ("AXButton", "AXLink"):
                for btn in _deep_find_all(web, role, max_depth=10):
                    try:
                        sub = str(getattr(btn, "AXSubrole", "") or "").strip()
                        t = str(getattr(btn, "AXTitle", "") or "").strip()
                    except Exception:
                        continue
                    is_close = (
                        sub == "AXCloseButton"
                        or t in ("关闭", "收起", "返回", "退出", "×", "X", "x")
                        or "关闭" in t
                    )
                    if is_close and _press(btn):
                        logger.info(
                            "浮层关闭：在 [%s] 子树点击 %s（title=%r sub=%r）",
                            desc, role, t, sub,
                        )
                        return True

        # 3: 浮层 WebArea 父节点附近找 AXCloseButton
        for desc, web in panel_webs:
            try:
                node = web
                for _ in range(4):
                    parent = getattr(node, "AXParent", None)
                    if parent is None:
                        break
                    for btn in _deep_find_all(parent, "AXButton", max_depth=3):
                        try:
                            sub = str(getattr(btn, "AXSubrole", "") or "").strip()
                        except Exception:
                            continue
                        if sub == "AXCloseButton" and _press(btn):
                            logger.info("浮层关闭：[%s] 父节点 AXCloseButton", desc)
                            return True
                    node = parent
            except Exception:
                continue

        # 4: AXCheckBox '收起' / '展开' 切换器（最可能有效的方式）
        # title 反映"下一次点击的动作"：'收起' = 当前展开状态，点了会收起
        # 优先 AXValue=0（直接设状态），再 PostToPid 鼠标点击（Chromium 对 AXPress
        # 不可靠，但接受真实鼠标事件），最后才 AXPress。
        for cb in _deep_find_all(window, "AXCheckBox", max_depth=10):
            try:
                title = str(getattr(cb, "AXTitle", "") or "").strip()
                if title not in ("收起", "展开"):
                    continue
                value = getattr(cb, "AXValue", None)
                logger.info("找到面板切换器 AXCheckBox title=%r value=%r", title, value)
                # value=1 表示当前展开（=要关掉的状态）
                if value not in (1, "1", True):
                    # 已经收起了；说明聊天面板没被这个浮层遮挡，可能是别的原因
                    continue

                # 4a: AXValue 赋值
                try:
                    cb.AXValue = 0
                    time.sleep(0.4)
                    new_val = getattr(cb, "AXValue", None)
                    if new_val in (0, "0", False):
                        logger.info("浮层关闭：AXCheckBox '%s' AXValue=0 已生效", title)
                        return True
                    else:
                        logger.debug("AXValue 赋值后值=%r，未生效，继续尝试", new_val)
                except Exception as exc:
                    logger.debug("AXValue 赋值失败：%s", exc)

                # 4b: PostToPid 鼠标点击 checkbox 中心
                try:
                    import Quartz
                    pid = _get_wecom_pid(settings.wecom_bundle_id)
                    pos = getattr(cb, "AXPosition", None)
                    sz = getattr(cb, "AXSize", None)
                    if pid and pos and sz:
                        cx = pos[0] + sz[0] / 2
                        cy = pos[1] + sz[1] / 2
                        pt = Quartz.CGPointMake(cx, cy)
                        for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                            ev = Quartz.CGEventCreateMouseEvent(
                                None, etype, pt, Quartz.kCGMouseButtonLeft,
                            )
                            Quartz.CGEventPostToPid(pid, ev)
                            time.sleep(0.05)
                        time.sleep(0.4)
                        new_val = getattr(cb, "AXValue", None)
                        if new_val in (0, "0", False):
                            logger.info(
                                "浮层关闭：PostToPid 点击 '%s' (%.0f,%.0f) 已生效",
                                title, cx, cy,
                            )
                            return True
                        else:
                            logger.debug("PostToPid 点击后值=%r，未生效", new_val)
                except Exception as exc:
                    logger.debug("PostToPid 鼠标点击 AXCheckBox 失败：%s", exc)

                # 4c: AXPress（最后兜底）
                if _press(cb):
                    time.sleep(0.4)
                    new_val = getattr(cb, "AXValue", None)
                    if new_val in (0, "0", False):
                        logger.info("浮层关闭：AXCheckBox '%s' AXPress 已生效", title)
                        return True
                    logger.debug("AXPress 后值=%r，未生效", new_val)
            except Exception:
                continue

        # 5: 兜底——发 ESC 给企微 pid
        try:
            import Quartz
            pid = _get_wecom_pid(settings.wecom_bundle_id)
            if pid:
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(None, 53, down)  # 53 = kVK_Escape
                    Quartz.CGEventPostToPid(pid, ev)
                logger.info("浮层关闭：兜底发 ESC 给 pid=%s", pid)
                return True
        except Exception as exc:
            logger.debug("ESC 兜底失败：%s", exc)

        logger.warning("所有关闭浮层的尝试都失败了，可能需要人工介入")
        return False

    def _find_conv_row_by_sender(self, expected_sender: str):
        """按 sender 名在当前 conv_list 重新定位 AXRow。

        必要原因：daemon 一轮 tick 内 parsed list 提前抓了一批 conv_row，
        但每次切换聊天后 conv_list 可能重渲染（badge 变化、行重排），
        导致 parsed 里的 row 引用 stale，AXPosition 拿不到、PostToPid
        点击失败。每次切换前用 sender 主体名重新查找一遍最稳。
        """
        if not expected_sender:
            return None
        sender_core = expected_sender.split("(")[0].strip()
        try:
            window = self._get_main_window()
            conv_list = self._find_conversation_list(window)
        except Exception:
            return None
        for row in self._get_conversation_rows(conv_list):
            try:
                for st in _deep_find_all(row, "AXStaticText", max_depth=4):
                    v = str(getattr(st, "AXValue", "") or "").strip()
                    if not v:
                        continue
                    # 完整匹配 / 主体名匹配
                    if v == expected_sender or v == sender_core:
                        return row
                    # 兼容预览中第一个 StaticText 是 sender_id（如 '刘明瑞(男)-3647'）
                    if sender_core and sender_core in v and "(" in v:
                        return row
            except Exception:
                continue
        return None

    def _switch_to_conv_and_verify(
        self, conv_row, expected_sender: str = "",
        per_attempt_timeout: float = 3.0, attempts: int = 3,
    ) -> bool:
        """点击会话行 → 轮询 panel desc 直到匹配 expected_sender。

        - expected_sender 含括号/性别/ID（如 '邢晓红(女)-7658'），
          匹配时只取首个 '(' 之前的主体（'邢晓红'），与 panel desc 双向 contains。
        - 单次尝试最多 per_attempt_timeout 秒；不匹配则尝试折叠功能浮层再点一次。
        - expected_sender 为空时不做校验，仅 press + 等 1.0s（向后兼容）。
        - 全部失败返回 False，调用方应放弃发送/读取，避免打到错误聊天。
        """
        sender_core = expected_sender.split("(")[0].strip() if expected_sender else ""

        # 检测到这种"非目标"的全局 desc 时，认定有功能浮层遮挡，先折叠再重试
        def _all_descs(window) -> list[str]:
            out = []
            try:
                for w in _deep_find_all(window, "AXWebArea", max_depth=15):
                    d = str(getattr(w, "AXDescription", "") or "").strip()
                    if d:
                        out.append(d)
            except Exception:
                pass
            return out

        pid = _get_wecom_pid(settings.wecom_bundle_id)

        for attempt in range(attempts):
            # 每次重试前重新定位 conv_row（避免 conv_list 重渲染导致的 stale ref）。
            # 不传 expected_sender 时无法重新定位，回退用传入的 row。
            if expected_sender:
                fresh = self._find_conv_row_by_sender(expected_sender)
                if fresh is not None:
                    conv_row = fresh

            # 切换会话：优先 PostToPid 鼠标点击（真实事件触发 visible panel 切换），
            # 失败再用 AX 级 Press。注意 _press_conv_row 走 AXSelected 兜底时
            # 只"选中"行不切换 panel——所以不能让它做主路径，否则 verify 会读到
            # stale chat header（上一个 sender 的）造成连环误判。
            pressed = False
            if pid:
                pressed = _force_click_conv_row(conv_row, pid)
            if not pressed:
                pressed = _press_conv_row(conv_row)
            if not pressed:
                logger.error("[%s] AX 与鼠标点击都未能切换会话", expected_sender or "?")
                if attempt + 1 >= attempts:
                    return False
                time.sleep(0.5)
                continue

            if not sender_core:
                time.sleep(1.0)
                return True

            deadline = time.monotonic() + per_attempt_timeout
            seen: set[str] = set()
            covering_panel = False  # 此次轮询是否看到功能浮层遮挡迹象
            while time.monotonic() < deadline:
                try:
                    window = self._get_main_window()
                except Exception:
                    time.sleep(0.2)
                    continue
                cur = self._active_chat_sender(window)
                if cur:
                    seen.add(cur)
                    if cur == sender_core or sender_core in cur or cur in sender_core:
                        logger.info(
                            "切换到 [%s] 成功（panel desc=%r，第 %d 次）",
                            expected_sender, cur, attempt + 1,
                        )
                        return True
                else:
                    # 当前没有"非功能浮层"的 chat WebArea；看一下是不是被功能浮层完全遮住
                    descs = _all_descs(window)
                    if descs and any(d in self._FUNCTION_PANEL_DESCS for d in descs):
                        covering_panel = True
                time.sleep(0.2)

            logger.warning(
                "切换到 [%s] 失败：%.1fs 内 panel desc 未匹配（core=%r seen=%s 浮层遮挡=%s），第 %d/%d 次",
                expected_sender, per_attempt_timeout, sender_core,
                sorted(seen)[:5], covering_panel, attempt + 1, attempts,
            )

            # 失败后若仍有重试机会，且检测到功能浮层遮挡，先折叠再下一轮
            if attempt + 1 < attempts and covering_panel:
                try:
                    window = self._get_main_window()
                    if self._try_dismiss_covering_panel(window):
                        logger.info("[%s] 已尝试关闭浮层，等待重试切换", expected_sender)
                        time.sleep(1.2)  # 等关闭动画 + 聊天面板重新渲染
                    else:
                        logger.warning(
                            "[%s] 浮层关闭失败，下一轮切换可能仍受遮挡",
                            expected_sender,
                        )
                except Exception as exc:
                    logger.debug("关闭浮层异常：%s", exc)

        logger.error(
            "[%s] 多次切换会话仍未让 panel 与目标 sender 一致，放弃此次操作",
            expected_sender,
        )
        return False

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

    def read_last_messages(self, conv_row, count: int, expected_sender: str = "") -> list[str]:
        """
        点击会话（静默），从右侧聊天面板读最近 count 条消息文本。
        最多重试 3 次，每次间隔 0.3 秒。失败返回 []。
        """
        if count <= 0:
            return []

        for attempt in range(3):
            result = self._try_read_last_messages(conv_row, count, expected_sender)
            if result:
                return result
            if attempt < 2:
                time.sleep(0.3)
        return []

    def _find_chat_scroll_area(self, window):
        """定位聊天消息区的 AXScrollArea（宽>800、顶部 y<500、高度最大）。

        企微 Mac 窗口固定有三个 ScrollArea：
          [0] 会话列表  (宽~250)
          [1] 聊天消息区 (宽~960, 高~1013)  ← 目标
          [2] 输入框区   (宽~960, 高~179)
        通过宽度 + 顶部位置 + 高度排除其他两个。
        """
        scrolls = _deep_find_all(window, "AXScrollArea", max_depth=10)
        candidates = []
        for s in scrolls:
            pos = getattr(s, "AXPosition", None)
            sz  = getattr(s, "AXSize", None)
            if not pos or not sz:
                continue
            if sz[0] > 800 and pos[1] < 500:   # 宽且靠顶
                candidates.append(s)
        if not candidates:
            return None
        # 取高度最大的（聊天区 ~1013px，输入框 ~179px）
        return max(candidates, key=lambda s: (getattr(s, "AXSize", None) or [0, 0])[1])

    def _try_read_last_messages(self, conv_row, count: int, expected_sender: str = "") -> list[str]:
        if not self._switch_to_conv_and_verify(conv_row, expected_sender):
            return []

        try:
            window = self._get_main_window()
        except Exception:
            return []

        chat_area = self._find_chat_scroll_area(window)
        if chat_area is None:
            logger.debug("read_last_messages: 未找到聊天消息 ScrollArea")
            return []

        panel_pos = getattr(chat_area, "AXPosition", None)
        panel_sz  = getattr(chat_area, "AXSize", None)
        if not panel_pos or not panel_sz:
            logger.debug("read_last_messages: 聊天 ScrollArea 无坐标")
            return []

        # 中线 x：左侧 = 客户消息，右侧 = 我方消息（含企微欢迎语）
        # 随窗口大小和位置动态计算，无需硬编码
        midx = panel_pos[0] + panel_sz[0] / 2

        tareas = _deep_find_all(chat_area, "AXTextArea", max_depth=15)
        logger.debug(
            "read_last_messages: 聊天区 AXTextArea %d 个，中线 x=%.0f",
            len(tareas), midx,
        )

        # 只保留客户消息（x < 中线）；我方消息（欢迎语、bot回复）在右侧，跳过
        incoming: list[tuple[float, str]] = []
        for t in tareas:
            pos = getattr(t, "AXPosition", None)
            val = str(getattr(t, "AXValue", "") or "").strip()
            if not val or not pos:
                continue
            if pos[0] >= midx:
                continue   # 右侧 = 我方，跳过
            incoming.append((pos[1], val))  # (y 坐标, 文本)

        if not incoming:
            logger.debug("read_last_messages: 聊天区无客户侧 AXTextArea")
            return []

        # 按 y 坐标分组，合并同行碎片（Chromium 有时把一条消息拆成多个 AXTextArea）
        from collections import defaultdict as _dd
        by_y: dict = _dd(list)
        for y, val in incoming:
            by_y[round(y)].append(val)

        messages: list[str] = []
        for y in sorted(by_y.keys()):
            text = "".join(by_y[y]).strip()
            if text and _is_message_text(text):
                messages.append(text)

        logger.debug(
            "read_last_messages: 客户消息 %d 行（原始碎片 %d 个），返回最后 %d 条",
            len(messages), len(incoming), min(count, len(messages)),
        )
        return messages[-count:] if messages else []

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

    def send_reply(
        self, reply_text: str, conv_row=None, expected_sender: str = "",
    ) -> tuple[bool, str]:
        """
        将回复文本写入输入框并发送（全程后台运行，不激活窗口）。

        发送前必须切换到目标会话，并通过 AXWebArea desc 校验 panel 与
        expected_sender 一致；不一致则放弃发送，避免把回复打到错误聊天。
        """
        try:
            self._get_app()
            window = self._get_main_window()
        except Exception as exc:
            logger.error("获取主窗口失败：%s", exc)
            return False, ""

        if conv_row is not None:
            if not self._switch_to_conv_and_verify(conv_row, expected_sender):
                logger.error(
                    "[%s] 切换会话或 panel 校验失败，放弃发送（不会误发到其他聊天）",
                    expected_sender or "?",
                )
                return False, ""
            # _switch_to_conv_and_verify 内部已 poll 等切换完成，无需额外 sleep

        # 找经营大厅等功能浮层的坐标范围，用于排除其内部的 text area
        # 与 _FUNCTION_PANEL_DESCS 共用，保证两条逻辑（识别+遮挡判定）一致
        def _scan_panel_rects():
            rects = []
            all_descs = []
            for w in _deep_find_all(window, "AXWebArea", max_depth=15):
                try:
                    desc = str(getattr(w, "AXDescription", "") or "").strip()
                    all_descs.append(desc)
                    if desc not in self._FUNCTION_PANEL_DESCS:
                        continue
                    pos = getattr(w, "AXPosition", None)
                    size = getattr(w, "AXSize", None)
                    if pos and size:
                        rects.append((pos[0], pos[1], pos[0] + size[0], pos[1] + size[1]))
                except Exception:
                    pass
            logger.info("AXWebArea 扫描：共 %d 个，desc=%s，命中浮层=%d 个",
                        len(all_descs), all_descs, len(rects))
            return rects

        panel_rects = _scan_panel_rects()

        # 功能浮层（经营大厅等）打开时，Chromium 键盘焦点在浮层 WebView，
        # CGEventPostToPid(Enter) 会路由到浮层而非聊天输入框，导致发送失败。
        # 解决方案：先通过 AX 折叠浮层，让焦点回到聊天区，再发送。
        if panel_rects:
            logger.info("检测到功能浮层（%d 个），尝试折叠以恢复键盘焦点…", len(panel_rects))
            _collapsed = False
            for cb in _deep_find_all(window, "AXCheckBox", max_depth=15):
                try:
                    title = str(getattr(cb, "AXTitle", "") or "").strip()
                    if title in ("展开", "收起"):
                        press_fn = getattr(cb, "Press", None)
                        if callable(press_fn):
                            press_fn()
                            logger.info("已按下折叠控件（AXCheckBox title='%s'）", title)
                            _collapsed = True
                            break
                except Exception as exc:
                    logger.debug("折叠控件操作失败: %s", exc)
            if _collapsed:
                # 等待 Chromium 处理折叠动画并把焦点切回聊天区
                time.sleep(1.5)
                panel_rects = _scan_panel_rects()
                if not panel_rects:
                    logger.info("功能浮层已折叠，键盘焦点应已恢复到聊天区")
                else:
                    logger.warning("折叠后浮层仍存在（panel_rects=%s），发送可能仍失败", panel_rects)
            else:
                logger.warning("未找到折叠控件（AXCheckBox），发送时 Enter 可能路由到浮层")

        def _in_panel_rect(node) -> bool:
            if not panel_rects:
                return False
            try:
                pos = getattr(node, "AXPosition", None)
                if pos:
                    nx, ny = pos[0], pos[1]
                    for (x0, y0, x1, y1) in panel_rects:
                        if x0 <= nx <= x1 and y0 <= ny <= y1:
                            return True
            except Exception:
                pass
            return False

        # 找聊天输入框：
        # WeCom 会话列表每行有一个 AXTextArea，值为 'BOT' 或 'BOT\u200b'（含零宽空格）。
        # 实际聊天输入框是另一个 AXTextArea，位于窗口右侧（x 坐标 > 1200）。
        # 策略：先用位置坐标筛（x > conversation_list_right），再按 BOT 标记过滤兜底。

        # 估算会话列表右边界（取所有 BOT 标记中最大 x + 元素宽度）
        conv_list_right = 0
        text_areas_all = _deep_find_all(window, "AXTextArea", max_depth=15)
        bot_count = 0
        for ta in text_areas_all:
            try:
                val = str(getattr(ta, "AXValue", "") or "")
                if val.replace('\u200b', '').strip() == "BOT":
                    bot_count += 1
                    pos = getattr(ta, "AXPosition", None)
                    sz = getattr(ta, "AXSize", None)
                    if pos and sz:
                        right = pos[0] + sz[0]
                        if right > conv_list_right:
                            conv_list_right = right
            except Exception:
                pass
        if conv_list_right == 0:
            conv_list_right = 900  # 经验值：会话列表通常在 900px 以内
        logger.info(
            "AXTextArea 总数=%d，BOT 标记=%d 个，会话列表右边界≈%d",
            len(text_areas_all), bot_count, conv_list_right,
        )

        # 候选：非 BOT 标记、不在功能浮层内
        # 输入框特征：width > 500 且 height > 50（消息气泡通常只有 9×22）
        candidates = []
        excluded_by_rect = []
        for ta in text_areas_all:
            try:
                val = str(getattr(ta, "AXValue", "") or "")
                # 跳过会话列表 BOT 标记（含零宽空格的变体）
                if val.replace('\u200b', '').strip() == "BOT":
                    continue
                if _in_panel_rect(ta):
                    excluded_by_rect.append(ta)
                    continue
                candidates.append(ta)
            except Exception:
                candidates.append(ta)

        # 优先取尺寸最大的（聊天输入框 ~960×118，消息气泡 ~9×22）
        def _area(ta):
            try:
                sz = getattr(ta, "AXSize", None)
                return (sz[0] * sz[1]) if sz else 0
            except Exception:
                return 0

        candidates.sort(key=_area)

        if not candidates:
            if excluded_by_rect:
                logger.warning(
                    "所有非 BOT AXTextArea(%d 个) 均被 panel_rect 过滤，回退使用最大的",
                    len(excluded_by_rect),
                )
                excluded_by_rect.sort(key=_area)
                candidates = excluded_by_rect
            else:
                logger.error(
                    "找不到聊天输入框（AXTextArea 共 %d 个，BOT=%d 个）",
                    len(text_areas_all), bot_count,
                )
                return False, ""

        # 优先选空值的 AXTextArea（聊天输入框通常为空，消息气泡有内容）
        empty_candidates = [
            c for c in candidates
            if not str(getattr(c, "AXValue", "") or "").strip()
        ]
        input_box = (empty_candidates[-1] if empty_candidates else candidates[-1])
        try:
            pos = getattr(input_box, "AXPosition", None)
            sz = getattr(input_box, "AXSize", None)
            val_preview = str(getattr(input_box, "AXValue", "") or "")[:30]
            logger.info(
                "选用输入框：pos=%s size=%s val=%r（共 %d 个候选，BOT=%d 个）",
                pos, sz, val_preview, len(candidates), bot_count,
            )
        except Exception:
            pass

        try:
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

            def _verify_sent():
                """检查输入框是否已清空（消息已发送）。
                只在 AXValue 能正常读取且值为空时返回 True；
                若读取异常或返回 None（AX 引用失效），一律返回 False，
                避免切前台后引用失效导致假阳性。
                """
                try:
                    raw = input_box.AXValue  # 会在失效时抛异常
                    box_val = str(raw or "")
                    return not box_val.strip()
                except Exception:
                    return False

            # ① 鼠标点击输入框中心（CGEventCreateMouseEvent + CGEventPostToPid）
            # probe 已验证：写文字 → PostToPid 鼠标点击 → PostToPid Enter → 成功发送
            # 注意：点击后不重写 AXValue、不设 AXFocused（会破坏 Chromium 内部焦点）
            if not enter_method:
                import Quartz
                pid = _get_wecom_pid(settings.wecom_bundle_id)
                if pid and pos and sz:
                    try:
                        cx = pos[0] + sz[0] / 2
                        cy = pos[1] + sz[1] / 2
                        logger.info("鼠标点击输入框：pid=%s 坐标=(%.0f, %.0f)", pid, cx, cy)
                        point = Quartz.CGPointMake(cx, cy)
                        ev_down = Quartz.CGEventCreateMouseEvent(
                            None, Quartz.kCGEventLeftMouseDown, point, Quartz.kCGMouseButtonLeft
                        )
                        Quartz.CGEventPostToPid(pid, ev_down)
                        time.sleep(0.05)
                        ev_up = Quartz.CGEventCreateMouseEvent(
                            None, Quartz.kCGEventLeftMouseUp, point, Quartz.kCGMouseButtonLeft
                        )
                        Quartz.CGEventPostToPid(pid, ev_up)
                        time.sleep(0.5)  # 等待 Chromium 处理点击并建立内部焦点（延长以适应聊天切换后状态）
                        val_before_enter = str(getattr(input_box, "AXValue", "") or "")
                        logger.info("PostToPid Enter 前输入框值：%r", val_before_enter[:30])
                        # 若点击导致文本被清除（select-all+overwrite），补写一次
                        if not val_before_enter.strip():
                            logger.info("点击后文本被清除，补写")
                            try:
                                setattr(input_box, "AXValue", reply_text)
                                time.sleep(0.05)
                            except Exception as exc:
                                logger.warning("补写文本失败：%s", exc)
                        # 直接发 Enter（鼠标点击已让 Chromium 获得内部焦点）
                        for down in (True, False):
                            ev = Quartz.CGEventCreateKeyboardEvent(None, 36, down)
                            Quartz.CGEventPostToPid(pid, ev)
                        time.sleep(0.3)
                        val_after_enter = str(getattr(input_box, "AXValue", "") or "")
                        logger.info("PostToPid Enter 后输入框值：%r", val_after_enter[:30])
                        if _verify_sent():
                            enter_method = f"MouseClick+Enter→pid{pid}"
                        else:
                            logger.warning(
                                "鼠标点击+Enter 后输入框仍有文本（%r），Enter 未生效",
                                val_after_enter[:30],
                            )
                    except Exception as exc:
                        logger.warning("鼠标点击+Enter 失败：%s", exc)
                else:
                    logger.info("跳过鼠标点击：pid=%s pos=%s sz=%s", pid, pos, sz)

            # ② 切前台 + PostToPid 鼠标 + PostToPid Enter
            # 逻辑：先让企微成为 OS key window（osascript set frontmost），
            # 再 PostToPid 鼠标点击输入框（让 Chromium 把内部焦点给输入框），
            # 再 PostToPid Enter（企微是 key window + Chromium 焦点在输入框 → 成功）。
            # probe 测试已证明"鼠标 PostToPid + Enter PostToPid"在企微刚激活时可行；
            # 此处主动先激活，确保条件成立。
            if not enter_method and pos and sz and pid:
                try:
                    import subprocess
                    # Step 1: 切前台，记录前任前台 app
                    make_front_script = (
                        'tell application "System Events"\n'
                        '    set prevApp to name of first process whose frontmost is true\n'
                        '    set frontmost of (first process whose bundle identifier is "'
                        + settings.wecom_bundle_id
                        + '") to true\n'
                        '    delay 0.2\n'
                        '    return prevApp\n'
                        'end tell\n'
                    )
                    r1 = subprocess.run(
                        ["osascript", "-e", make_front_script],
                        capture_output=True, text=True, timeout=3,
                    )
                    prev_app = r1.stdout.strip()
                    logger.info("切前台完成，prev=%r", prev_app)

                    # Step 2: PostToPid 鼠标点击输入框（企微已是前台，Chromium 会接受焦点）
                    import Quartz as _Q
                    cx2 = pos[0] + sz[0] / 2
                    cy2 = pos[1] + sz[1] / 2
                    pt = _Q.CGPointMake(cx2, cy2)
                    _Q.CGEventPostToPid(pid, _Q.CGEventCreateMouseEvent(
                        None, _Q.kCGEventLeftMouseDown, pt, _Q.kCGMouseButtonLeft))
                    time.sleep(0.05)
                    _Q.CGEventPostToPid(pid, _Q.CGEventCreateMouseEvent(
                        None, _Q.kCGEventLeftMouseUp, pt, _Q.kCGMouseButtonLeft))
                    time.sleep(0.1)

                    # Step 3: 重写文本（点击可能清除原内容）
                    try:
                        setattr(input_box, "AXValue", reply_text)
                        time.sleep(0.05)
                    except Exception:
                        pass

                    # Step 4: PostToPid Enter（企微是 key window，Chromium 焦点在输入框）
                    logger.info("flash-front+PostToPid-mouse+Enter（坐标=%.0f,%.0f）", cx2, cy2)
                    for down in (True, False):
                        ev = _Q.CGEventCreateKeyboardEvent(None, 36, down)
                        _Q.CGEventPostToPid(pid, ev)
                    time.sleep(0.3)

                    # Step 5: 切回原前台
                    if prev_app:
                        try:
                            subprocess.run(
                                ["osascript", "-e",
                                 f'tell application "System Events" to set frontmost of process "{prev_app}" to true'],
                                capture_output=True, timeout=2,
                            )
                        except Exception:
                            pass

                    if _verify_sent():
                        enter_method = "flash-front+PostToPid-mouse+Enter"
                    else:
                        logger.warning("flash-front+PostToPid-mouse+Enter 后输入框仍有文本")
                except Exception as exc:
                    logger.warning("flash-front+PostToPid-mouse+Enter 异常：%s", exc)

            # ③ sendKeys("\r") 兜底
            if not enter_method:
                try:
                    input_box.sendKeys("\r")
                    time.sleep(0.3)
                    if _verify_sent():
                        enter_method = "sendKeys(\\r)"
                    else:
                        logger.warning("sendKeys 回车后输入框仍有文本，Enter 未生效")
                except Exception as exc:
                    logger.debug("sendKeys 回车失败：%s", exc)

            method_label = f"{used_method}+{enter_method}"
            if enter_method:
                logger.info("已发送回复（方式=%s）：%s", method_label, reply_text[:60])
                return True, method_label
            else:
                logger.error("所有发送方式均失败（输入框未清空），放弃")
                return False, ""
        except Exception as exc:
            logger.error("发送回复失败：%s", exc)
            return False, ""

    # ------------------------------------------------------------------
    # 经营线索 处理
    # ------------------------------------------------------------------

    def _find_xiansuo_links(self, chat_scroll) -> list:
        """在聊天滚动区域找所有 '线索详情>>' AXLink 元素（只遍历已渲染的可见行）。"""
        links = []
        try:
            for child in _safe_children(chat_scroll):
                if str(getattr(child, "AXRole", "") or "") != "AXTable":
                    continue
                for row in _safe_children(child):
                    if str(getattr(row, "AXRole", "") or "") != "AXRow":
                        continue
                    for cell in _safe_children(row):
                        for ck in _safe_children(cell):
                            if str(getattr(ck, "AXRole", "") or "") != "AXTextArea":
                                continue
                            for lk in _safe_children(ck):
                                if str(getattr(lk, "AXRole", "") or "") == "AXLink":
                                    title = str(getattr(lk, "AXTitle", "") or "")
                                    if "线索详情" in title:
                                        links.append(lk)
        except Exception as exc:
            logger.debug("_find_xiansuo_links: %s", exc)
        return links

    def _find_lead_popup(self):
        """找当前打开的线索 popup 窗口。

        优先匹配"经营线索详情"（已添加企微的标准 popup）；其次回退到任意
        尺寸合理的非主窗口（未添加企微的精简 popup 标题/结构不同，但也是
        非主窗口的 AXWindow）。
        """
        try:
            wins = self._get_app().AXWindows
        except Exception:
            return None

        candidates = []
        for w in wins:
            try:
                title = str(getattr(w, "AXTitle", "") or "").strip()
                sz = getattr(w, "AXSize", None)
            except Exception:
                continue
            if title == "企业微信":
                continue
            if not sz or sz[0] < 300 or sz[1] < 300:
                continue
            candidates.append((sz, w))

            # 优先匹配 '经营线索详情' 标题
            for st in _deep_find_all(w, "AXStaticText", max_depth=8):
                try:
                    v = str(getattr(st, "AXValue", "") or "").strip()
                except Exception:
                    continue
                if v == "经营线索详情":
                    return w

        # 回退：用最大尺寸的非主窗口（兼容未添加企微的简化 popup）
        if candidates:
            candidates.sort(key=lambda x: x[0][0] * x[0][1], reverse=True)
            logger.info(
                "经营线索：未匹配到 '经营线索详情' popup，回退用最大非主窗口 sz=%s",
                candidates[0][0],
            )
            return candidates[0][1]
        return None

    def _expand_member_info_section(self, popup, pid) -> bool:
        """点击"会员信息"区下的'点击展开'，展开后能看到'是否添加企微'状态。

        popup 里有 3 个'点击展开'，按 y 坐标升序，第一个属于会员信息区。
        StaticText 自身不响应 PostToPid 鼠标点击的可能性较高，直接点
        中心坐标即可（实测 PostToPid 鼠标对 Chromium 内嵌网页的 click
        很可靠）。
        """
        import Quartz
        expand_sts = []
        for st in _deep_find_all(popup, "AXStaticText", max_depth=8):
            try:
                v = str(getattr(st, "AXValue", "") or "").strip()
                if v != "点击展开":
                    continue
                pos = getattr(st, "AXPosition", None)
                sz = getattr(st, "AXSize", None)
                if pos and sz:
                    expand_sts.append((pos, sz))
            except Exception:
                continue
        if not expand_sts or not pid:
            logger.debug("经营线索：未找到'点击展开'或缺 pid")
            return False
        expand_sts.sort(key=lambda x: x[0][1])  # y 升序
        pos, sz = expand_sts[0]  # 最上面的 = 会员信息下的
        cx = pos[0] + sz[0] / 2
        cy = pos[1] + sz[1] / 2
        try:
            pt = Quartz.CGPointMake(cx, cy)
            for et in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                ev = Quartz.CGEventCreateMouseEvent(None, et, pt, Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPostToPid(pid, ev)
                time.sleep(0.05)
            logger.info("经营线索：点击会员信息区 '点击展开' (%.0f, %.0f)", cx, cy)
            return True
        except Exception as exc:
            logger.warning("经营线索：点击 点击展开 失败：%s", exc)
            return False

    def _read_qiwei_status(self, popup) -> str:
        """读取 popup 里"是否添加企微"的状态。

        实际 UI 格式未知（可能 'label + 是/否'，也可能 'label: 是'），
        先把 popup 里 y < 1500 范围所有 StaticText 按位置 dump 到日志，
        作为研究素材；同时尝试基于"添加企微"关键词附近找答案。
        """
        all_st = []
        for st in _deep_find_all(popup, "AXStaticText", max_depth=8):
            try:
                v = str(getattr(st, "AXValue", "") or "").strip()
                pos = getattr(st, "AXPosition", None)
                if v and pos and pos[1] < 1500:
                    all_st.append((pos[1], pos[0], v))
            except Exception:
                continue
        all_st.sort()

        # 诊断输出（前 30 条），方便研究展开后结构
        logger.info("经营线索：popup StaticText 共 %d 条:", len(all_st))
        for y, x, v in all_st[:30]:
            logger.info("  y=%.0f x=%.0f %r", y, x, v)

        # 策略 1：完整句子里直接含"未添加企微"/"已添加企微"等
        for y, x, v in all_st:
            if "未添加企微" in v or "客户未添加" in v or "尚未添加" in v:
                return "未添加"
            if "已添加企微" in v:
                return "已添加"

        # 策略 2：label + 值 结构（"是否添加企微" → "是"/"否"）
        for i, (y, x, v) in enumerate(all_st):
            if "添加企微" not in v and "微信" not in v:
                continue
            # 右侧相邻（同 y±10，x 更大）
            for y2, x2, v2 in all_st:
                if abs(y2 - y) < 10 and x2 > x and v2 in ("是", "否", "已添加", "未添加"):
                    return v2
            # 下方相邻（y+30~80, 接近的 x）
            for y2, x2, v2 in all_st:
                if 20 < y2 - y < 80 and abs(x2 - x) < 50 and v2 in ("是", "否", "已添加", "未添加"):
                    return v2
        return "unknown"

    def _find_qulianxi(self, window):
        """在线索详情 popup 窗口里找 '去联系' 按钮（实测就是 AXButton）。

        优化前：扫所有窗口（含主窗口）的 AXButton+AXLink+AXStaticText，深度 8，
        单次轮询 ~70s，15 次轮询要 17.5 分钟，期间 daemon 卡死。
        优化后：跳过主窗口（去联系只在 popup 里）、主搜 AXButton、深度 6，
        单次 ~1-2s。
        """
        try:
            app = self._get_app()
            all_wins = app.AXWindows
        except Exception:
            return None

        # 筛选 popup 窗口（非主窗口，且有合理尺寸）
        popup_wins = []
        for w in all_wins:
            try:
                title = str(getattr(w, "AXTitle", "") or "").strip()
                sz = getattr(w, "AXSize", None)
            except Exception:
                continue
            if title == "企业微信":
                continue
            if not sz or sz[0] < 300 or sz[1] < 300:
                continue
            popup_wins.append((title, w))

        if not popup_wins:
            return None

        # 主路径：扫 popup 的 AXButton
        for title, w in popup_wins:
            for btn in _deep_find_all(w, "AXButton", max_depth=6):
                try:
                    title_e = str(getattr(btn, "AXTitle", "") or "")
                except Exception:
                    continue
                if "去联系" in title_e:
                    pos = getattr(btn, "AXPosition", None)
                    logger.info(
                        "经营线索：找到 AXButton '去联系' at %s (popup title=%r)",
                        pos, title,
                    )
                    return btn

        # 兜底：popup 内 AXLink/AXStaticText（极少见情况）
        for title, w in popup_wins:
            for role in ("AXLink", "AXStaticText"):
                for elem in _deep_find_all(w, role, max_depth=6):
                    try:
                        t = str(getattr(elem, "AXTitle", "") or "")
                        v = str(getattr(elem, "AXValue", "") or "")
                    except Exception:
                        continue
                    if "去联系" in t or "去联系" in v:
                        pos = getattr(elem, "AXPosition", None)
                        logger.info(
                            "经营线索：找到 %s '去联系' at %s (popup title=%r)",
                            role, pos, title,
                        )
                        return elem
        return None

    def _handle_jingying_leads(self, conv_row) -> None:
        """
        经营线索处理：切换到经营线索聊天 → 找最新未处理线索 → 点击"线索详情>>"
        → 等待 webview 加载 → 点击"去联系"。
        """
        import Quartz

        logger.info("经营线索：开始处理线索…")

        if not _press_conv_row(conv_row):
            logger.warning("经营线索：切换聊天窗口失败")
            return
        time.sleep(0.8)

        # 先关闭可能存在的残留 popup（上一轮失败留下的"复制话术..."等弹窗），
        # 否则 WeCom 可能把它误当成"已打开"，不真正渲染新的线索详情 popup。
        self._close_xiansuo_popups()
        time.sleep(0.5)

        try:
            window = self._get_main_window()
        except Exception as exc:
            logger.warning("经营线索：获取主窗口失败：%s", exc)
            return

        pid = _get_wecom_pid(settings.wecom_bundle_id)
        if not pid:
            logger.warning("经营线索：无法获取 WeCom PID")
            return

        # 找聊天消息滚动区
        chat_scroll = self._find_chat_scroll_area(window)
        if chat_scroll is None:
            logger.warning("经营线索：未找到聊天滚动区")
            return

        # 找所有"线索详情>>"链接
        links = self._find_xiansuo_links(chat_scroll)
        logger.info("经营线索：找到 %d 个线索详情链接", len(links))
        if not links:
            return

        # 取最后一条（最新线索），检查是否已处理过
        lk = links[-1]
        try:
            parent_val = str(getattr(lk.AXParent, "AXValue", "") or "")
        except Exception:
            parent_val = repr(getattr(lk, "AXPosition", ""))
        lead_hash = hashlib.sha256(parent_val.encode()).hexdigest()

        if lead_hash in self._processed_leads:
            logger.info("经营线索：最新线索已处理过，跳过")
            return

        # 根据消息文本里"微信昵称:"字段判断是否已添加企微：
        # - 已添加：'微信昵称:某某'  → 走开 popup + 点 去联系
        # - 未添加：'微信昵称:\n'    → 直接跳过，省 ~13s popup 耗时
        traveler, qiwei_nick = _parse_lead_message(parent_val)
        if qiwei_nick == "":
            self._processed_leads.add(lead_hash)
            logger.info(
                "经营线索：客户【未添加企微】✗（旅客=%s, 微信昵称为空），跳过",
                traveler or "?",
            )
            return
        logger.info(
            "经营线索：客户【已添加企微】✓（旅客=%s, 微信昵称=%s）",
            traveler or "?", qiwei_nick,
        )

        # 点击"线索详情>>"：双管齐下——先 AXPress 触发 webview 内部 click
        # （Chromium 链接对 AXPress 最敏感），再 PostToPid 鼠标点 link 真实
        # 中心兜底。两个都试，无论哪个生效都行。
        lk_pos = getattr(lk, "AXPosition", None)
        lk_sz = getattr(lk, "AXSize", None)

        # AXPress 尝试
        ax_pressed = False
        for ax_action in ("Press", "AXPress"):
            fn = getattr(lk, ax_action, None)
            if callable(fn):
                try:
                    fn()
                    ax_pressed = True
                    logger.info("经营线索：AXPress 线索详情")
                    break
                except Exception as exc:
                    logger.debug("经营线索：%s 线索详情失败：%s", ax_action, exc)

        # PostToPid 鼠标点 link 中心
        mouse_clicked = False
        if lk_pos and lk_sz:
            try:
                cx = lk_pos[0] + lk_sz[0] / 2
                cy = lk_pos[1] + lk_sz[1] / 2
                pt = Quartz.CGPointMake(cx, cy)
                for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                    ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
                    Quartz.CGEventPostToPid(pid, ev)
                    time.sleep(0.05)
                mouse_clicked = True
                logger.info(
                    "经营线索：PostToPid 鼠标点击线索详情 中心=(%.0f, %.0f) sz=%s",
                    cx, cy, lk_sz,
                )
            except Exception as exc:
                logger.debug("经营线索：PostToPid 点击线索详情异常：%s", exc)

        if not (ax_pressed or mouse_clicked):
            logger.warning("经营线索：无法点击线索详情链接（AXPress 和 鼠标都失败）")
            return

        # 等 popup 加载完成。就绪 = 存在某个非主窗口含 "话术" label。
        # 给 Chromium 起一下 + 长 timeout（话术区在 popup 底部，渲染慢）。
        time.sleep(2.0)
        # 先快速试一下：popup 顶部加载时话术可能已在 AX 树
        popup_win = self._wait_for_signal(
            "话术", mode="static", timeout=4.0, poll_interval=0.8,
        )
        # 不在视口里 → 滚动 popup 到底部，让"话术"label 出现在 AX 树
        if popup_win is None:
            logger.info("经营线索：popup 顶部未见'话术' label，滚动到底部再找")
            self._scroll_popup_to_bottom(pid)
            popup_win = self._wait_for_signal(
                "话术", mode="static", timeout=15.0, poll_interval=1.0,
            )

        if popup_win is None:
            # 诊断 dump：popup 里到底有什么 StaticText 和 Button
            logger.warning(
                "经营线索：popup 未在 20s 内出现'话术' label，放弃 hash=%s",
                lead_hash[:12],
            )
            self._dump_current_popups("没找到话术 label 诊断")
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return
        logger.info("经营线索：popup 已加载完成（'话术' label 已出现）")

        # === 新流程：话术 → 去发送 → 复制话术并跳转 → 确认跳转 → 粘贴发送 ===

        # 1) 找话术区下第一个"去发送"按钮（话术 label 之下 y 最小者）
        send_btn = self._find_first_huashu_send_btn(popup_win)
        if send_btn is None:
            logger.warning(
                "经营线索：popup 有'话术' label 但未找到下方'去发送'按钮，hash=%s",
                lead_hash[:12],
            )
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return
        if not self._press(send_btn, pid):
            logger.warning("经营线索：点击话术'去发送'失败")
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return
        logger.info("经营线索：点击话术'去发送'")

        # 2) 等 popup 切到"复制话术并跳转"状态，点击该按钮
        time.sleep(1.0)  # popup 切换缓冲
        if not self._click_button_with_retry(
            "复制话术并跳转", "复制话术并跳转至单聊窗口", pid, wait_timeout=8.0,
        ):
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return

        # 3) 等"确认跳转"对话框，点击
        time.sleep(0.8)
        if not self._click_button_with_retry(
            "确认跳转", "确认跳转", pid, wait_timeout=6.0,
        ):
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return

        # 4) 等 WeCom 切到客户单聊页（chat header 必须出现旅客名才能粘贴）
        if not self._wait_chat_header_contains(traveler, timeout=8.0):
            logger.warning(
                "经营线索：8s 内未切到 %r 的单聊页，**放弃粘贴发送**（避免误发其他聊天），hash=%s",
                traveler, lead_hash[:12],
            )
            self._processed_leads.add(lead_hash)
            self._close_xiansuo_popups()
            return

        # 5) Cmd+V 粘贴 + Enter 发送
        if self._paste_and_send_in_chat(pid):
            logger.info(
                "经营线索：✅ 全流程完成 旅客=%s hash=%s 话术已发送",
                traveler, lead_hash[:12],
            )
        else:
            logger.warning(
                "经营线索：粘贴/发送话术失败 hash=%s", lead_hash[:12],
            )

        self._processed_leads.add(lead_hash)
        time.sleep(0.5)
        self._close_xiansuo_popups()

    # ------------------------------------------------------------------
    # 话术发送流程 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _press(node, pid: int | None = None) -> bool:
        """点击 AX 节点：优先 AXPress / Press；失败用 PostToPid 鼠标坐标点击中心兜底。

        实测 popup 里 AXPress 在 daemon 上下文偶发失败（Chromium 内嵌按钮在
        popup 重渲染时引用 stale），用真实鼠标事件兜底能突破。
        """
        # 提前读取坐标，避免 node 后续失效
        pos = None
        sz = None
        try:
            pos = getattr(node, "AXPosition", None)
            sz = getattr(node, "AXSize", None)
        except Exception:
            pass

        for action in ("Press", "AXPress"):
            fn = getattr(node, action, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except Exception:
                    continue

        # PostToPid 鼠标坐标点击 fallback
        if pid and pos and sz:
            try:
                import Quartz
                cx = pos[0] + sz[0] / 2
                cy = pos[1] + sz[1] / 2
                pt = Quartz.CGPointMake(cx, cy)
                for et in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                    ev = Quartz.CGEventCreateMouseEvent(None, et, pt, Quartz.kCGMouseButtonLeft)
                    Quartz.CGEventPostToPid(pid, ev)
                    time.sleep(0.05)
                return True
            except Exception:
                pass
        return False

    def _click_button_with_retry(
        self, title_substring: str, label: str, pid: int,
        wait_timeout: float = 12.0,
    ) -> bool:
        """等找到 title 含 substring 的 AXButton，settled 0.4s 后 fresh re-find 再 press。

        re-find 是关键——find 到 press 之间 popup 可能重渲染，旧引用 stale，
        重新找一次拿最新引用再 press。
        """
        deadline = time.monotonic() + wait_timeout
        btn = None
        while time.monotonic() < deadline:
            btn = self._find_button_in_popups(title_substring)
            if btn is not None:
                break
            time.sleep(0.5)
        if btn is None:
            logger.warning("经营线索：'%s' 按钮未在 %.1fs 内出现", label, wait_timeout)
            self._dump_current_popups(f"没找到'{label}'按钮 诊断")
            return False
        time.sleep(0.4)
        # 关键：fresh re-find，避免 AXUIElement 引用 stale
        btn_fresh = self._find_button_in_popups(title_substring) or btn
        if not self._press(btn_fresh, pid):
            logger.warning("经营线索：点击'%s'失败", label)
            return False
        logger.info("经营线索：点击'%s'", label)
        return True

    def _wait_chat_header_contains(self, traveler_core: str, timeout: float = 6.0) -> bool:
        """等主窗口 chat header 显示 traveler 名（用 _active_chat_sender 兜底逻辑）。"""
        if not traveler_core:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                window = self._get_main_window()
                sender = self._active_chat_sender(window)
                if sender and traveler_core in sender:
                    logger.info(
                        "经营线索：单聊页已就绪（chat header=%r 包含旅客=%r）",
                        sender, traveler_core,
                    )
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def _scroll_popup_to_bottom(self, pid) -> None:
        """让 popup 滚到底部，让话术区域出现在 AX 树。

        策略：先点击 popup 中部让它获得键盘焦点，再连发几次 PageDown，
        最后发 End 键确保到底。
        """
        import Quartz
        # 找当前 popup 中心点
        try:
            wins = self._get_app().AXWindows
        except Exception:
            return
        popup = None
        for w in wins:
            try:
                wt = str(getattr(w, "AXTitle", "") or "").strip()
                sz = getattr(w, "AXSize", None)
                pos = getattr(w, "AXPosition", None)
            except Exception:
                continue
            if wt == "企业微信" or not sz or not pos or sz[0] < 300:
                continue
            popup = (pos, sz)
            break
        if popup is None:
            return

        pos, sz = popup
        cx = pos[0] + sz[0] / 2
        cy = pos[1] + sz[1] / 2

        try:
            # 鼠标点中部聚焦
            pt = Quartz.CGPointMake(cx, cy)
            for et in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                ev = Quartz.CGEventCreateMouseEvent(None, et, pt, Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPostToPid(pid, ev)
                time.sleep(0.05)
            time.sleep(0.3)

            # 连续 PageDown ×6（keycode 121 = PageDown）确保滚到底
            for _ in range(6):
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(None, 121, down)
                    Quartz.CGEventPostToPid(pid, ev)
                    time.sleep(0.03)
                time.sleep(0.15)
            # End 键（keycode 119）
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 119, down)
                Quartz.CGEventPostToPid(pid, ev)
                time.sleep(0.05)
            time.sleep(0.4)
            logger.info("经营线索：popup 已滚动到底部")
        except Exception as exc:
            logger.debug("经营线索：popup 滚动异常：%s", exc)

    def _dump_current_popups(self, reason: str) -> None:
        """诊断用：dump 所有非主窗口（popup）里 y<800 的 button/text 到 log。"""
        try:
            wins = self._get_app().AXWindows
        except Exception:
            return
        logger.info("=== popup 诊断: %s ===", reason)
        for i, w in enumerate(wins):
            try:
                wt = str(getattr(w, "AXTitle", "") or "").strip()
                pos = getattr(w, "AXPosition", None)
                sz = getattr(w, "AXSize", None)
            except Exception:
                continue
            if wt == "企业微信":
                continue
            logger.info("  popup[%d] title=%r pos=%s sz=%s", i, wt, pos, sz)
            # dump 浅层 button + statictext
            elems = []
            for role in ("AXButton", "AXStaticText"):
                for el in _deep_find_all(w, role, max_depth=8):
                    try:
                        pos = getattr(el, "AXPosition", None)
                        if not pos:
                            continue
                        t = str(getattr(el, "AXTitle", "") or "")[:40]
                        v = str(getattr(el, "AXValue", "") or "")[:40]
                        if not (t or v):
                            continue
                        elems.append((pos[1], pos[0], role, t, v))
                    except Exception:
                        continue
            elems.sort()
            for y, x, role, t, v in elems[:25]:
                logger.info("    y=%.0f x=%.0f [%s] title=%r val=%r", y, x, role, t, v)

    def _find_first_huashu_send_btn(self, popup):
        """popup 里找'话术' label 之下第一个 AXButton title='去发送'。"""
        huashu_y = None
        for st in _deep_find_all(popup, "AXStaticText", max_depth=8):
            try:
                v = str(getattr(st, "AXValue", "") or "").strip()
                if v == "话术":
                    pos = getattr(st, "AXPosition", None)
                    if pos:
                        huashu_y = pos[1]
                        break
            except Exception:
                continue
        if huashu_y is None:
            return None
        candidates = []
        for btn in _deep_find_all(popup, "AXButton", max_depth=8):
            try:
                t = str(getattr(btn, "AXTitle", "") or "").strip()
                if t == "去发送":
                    pos = getattr(btn, "AXPosition", None)
                    if pos and pos[1] > huashu_y:
                        candidates.append((pos[1], btn))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    def _find_button_in_popups(self, title_substring: str):
        """在所有非主窗口里找 title 含 substring 的 AXButton（第一个）。"""
        try:
            wins = self._get_app().AXWindows
        except Exception:
            return None
        for w in wins:
            try:
                wt = str(getattr(w, "AXTitle", "") or "").strip()
                sz = getattr(w, "AXSize", None)
            except Exception:
                continue
            if wt == "企业微信":
                continue
            if not sz or sz[0] < 300:
                continue
            for btn in _deep_find_all(w, "AXButton", max_depth=8):
                try:
                    t = str(getattr(btn, "AXTitle", "") or "")
                    if title_substring in t:
                        return btn
                except Exception:
                    continue
        return None

    def _find_popup_with_signal(self, signal_label: str):
        """在所有非主窗口里找含 signal_label（AXStaticText.AXValue == signal_label）
        的 popup。signal_label 是"该 popup 已完成加载并进入特定状态"的标识。

        depth=14 实测在 popup 重渲染场景下慢，降到 8（popup 内文字 label 通常浅）。
        """
        try:
            wins = self._get_app().AXWindows
        except Exception:
            return None
        for w in wins:
            try:
                wt = str(getattr(w, "AXTitle", "") or "").strip()
                sz = getattr(w, "AXSize", None)
            except Exception:
                continue
            if wt == "企业微信":
                continue
            if not sz or sz[0] < 300:
                continue
            for st in _deep_find_all(w, "AXStaticText", max_depth=8):
                try:
                    v = str(getattr(st, "AXValue", "") or "").strip()
                    if v == signal_label:
                        return w
                except Exception:
                    continue
        return None

    def _wait_for_signal(self, signal_label: str, mode: str = "static",
                         timeout: float = 12.0, poll_interval: float = 0.5):
        """轮询直到找到 popup 含 signal_label 元素，返回 popup 或 None。

        mode='static': 找 AXStaticText.AXValue == signal_label 的 popup
        mode='button': 找 AXButton.AXTitle contains signal_label 的 popup
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mode == "static":
                w = self._find_popup_with_signal(signal_label)
            else:
                # button 模式：返回含目标按钮的 popup
                btn = self._find_button_in_popups(signal_label)
                if btn is not None:
                    try:
                        # 走到 popup window 这一层
                        node = btn
                        for _ in range(20):
                            parent = getattr(node, "AXParent", None)
                            if parent is None:
                                break
                            role = str(getattr(parent, "AXRole", "") or "")
                            if role == "AXWindow":
                                return parent
                            node = parent
                    except Exception:
                        pass
                    return btn  # 兜底返回按钮本身（其实没用到）
                w = None
            if w is not None:
                return w
            time.sleep(poll_interval)
        return None

    def _paste_and_send_in_chat(self, pid) -> bool:
        """在当前活跃聊天的输入框 Cmd+V 粘贴 + Enter 发送。

        实现：用 osascript 切 WeCom 前台 → keystroke "v" with Cmd → Enter。
        放弃用 atomacos 找输入框，因为在线索详情流程后 AX 树扫描容易卡死
        （_deep_find_all 在深层节点上 hang 数十秒）；osascript 走系统级
        AppleEvent，直接 keystroke 给前台 app 不需要找节点，最稳定。

        前提：调用方必须先 _wait_chat_header_contains 确认当前聊天是目标
        客户，否则 keystroke 会发到错误聊天。
        """
        import subprocess
        script = (
            'tell application "System Events"\n'
            '    tell process "企业微信"\n'
            '        set frontmost to true\n'
            '        delay 0.5\n'
            '        keystroke "v" using {command down}\n'
            '        delay 0.6\n'
            '        keystroke return\n'
            '    end tell\n'
            'end tell\n'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0:
                logger.info("经营线索：osascript Cmd+V + Enter 完成")
                return True
            logger.warning(
                "经营线索：osascript 粘贴发送失败 rc=%s stderr=%s",
                r.returncode, r.stderr.strip()[:120],
            )
        except subprocess.TimeoutExpired:
            logger.warning("经营线索：osascript 粘贴发送超时")
        except Exception as exc:
            logger.warning("经营线索：osascript 粘贴发送异常：%s", exc)
        return False

    def _click_confirm_dialog(self, pid) -> None:
        """点击"确定"/"确定跳转" 确认弹窗。"""
        import Quartz
        try:
            app = self._get_app()
            all_wins = app.AXWindows
        except Exception:
            return

        for w in all_wins:
            for btn in _deep_find_all(w, "AXButton", max_depth=6):
                title = str(getattr(btn, "AXTitle", "") or "").strip()
                if title in ("确定", "确定跳转", "跳转", "OK"):
                    logger.info("经营线索：找到确认按钮 '%s'，点击", title)
                    # 先尝试 AXPress
                    for ax_action in ("Press", "AXPress"):
                        fn = getattr(btn, ax_action, None)
                        if callable(fn):
                            try:
                                fn()
                                logger.info("经营线索：%s 确认成功", ax_action)
                                return
                            except Exception as exc:
                                logger.debug("经营线索：%s 确认失败：%s", ax_action, exc)
                    # 兜底：坐标点击
                    btn_pos = getattr(btn, "AXPosition", None)
                    btn_sz  = getattr(btn, "AXSize", None)
                    if btn_pos and btn_sz and pid:
                        cx = btn_pos[0] + btn_sz[0] / 2
                        cy = btn_pos[1] + btn_sz[1] / 2
                        pt = Quartz.CGPointMake(cx, cy)
                        for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                            ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
                            Quartz.CGEventPostToPid(pid, ev)
                            time.sleep(0.05)
                        logger.info("经营线索：PostToPid 确认 (%.0f, %.0f)", cx, cy)
                    return

        logger.debug("经营线索：未找到确认弹窗（可能不需要确认）")

    def _close_xiansuo_popups(self) -> None:
        """关闭所有残留的线索详情弹窗（非主窗口）。"""
        try:
            app = self._get_app()
            all_wins = list(app.AXWindows)
            # 主窗口 = 宽度最大的那个
            main_win = max(
                all_wins,
                key=lambda w: (getattr(w, "AXSize", None) or [0])[0],
                default=None,
            )
            main_w = (getattr(main_win, "AXSize", None) or [0])[0] if main_win else 0

            closed = 0
            for w in all_wins:
                w_sz = getattr(w, "AXSize", None)
                if not w_sz:
                    continue
                # 跳过主窗口
                if w_sz[0] >= main_w * 0.9:
                    continue
                # 先试 AXCloseButton subrole
                for btn in _deep_find_all(w, "AXButton", max_depth=3):
                    sub = str(getattr(btn, "AXSubrole", "") or "").strip()
                    if sub == "AXCloseButton":
                        for action in ("Press", "AXPress"):
                            fn = getattr(btn, action, None)
                            if callable(fn):
                                try:
                                    fn()
                                    closed += 1
                                    logger.info("经营线索：关闭弹窗（sz=%s）", w_sz)
                                    break
                                except Exception:
                                    pass
                        break
                else:
                    # 没找到 AXCloseButton，用 AXCancel 或 ESC
                    try:
                        w.AXCancel()
                        closed += 1
                    except Exception:
                        pass

            if closed:
                logger.info("经营线索：共关闭 %d 个弹窗", closed)
            else:
                logger.debug("经营线索：无弹窗可关闭（共 %d 个窗口）", len(all_wins))
        except Exception as exc:
            logger.debug("_close_xiansuo_popups: %s", exc)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """单次轮询：检查未读 → 提取 → 回复 → 记录。

        注意：全程后台运行，不抢占前台，不激活窗口。
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

            # 经营线索：特殊处理——点击线索详情 → 去联系，不走普通回复流程
            if "经营线索" in sender:
                self._handle_jingying_leads(msg["conv_row"])
                self._last_text_by_sender[sender] = msg["text"]
                continue

            if settings.is_sender_excluded(sender):
                logger.info("排除名单命中，跳过 [%s]", sender)
                self._last_text_by_sender[sender] = msg["text"]
                continue

            # 出方向欢迎语过滤：preview 无法区分出/入方向，企微在客户经理新加好友
            # 后会推欢迎语模板（"我已经添加了你..."、"尊敬的客户：感谢您选择南方航空..."），
            # 这些不是客户消息，bot 不应回复。
            if self._is_outgoing_template(msg["text"]):
                logger.info(
                    "跳过 [%s]：preview 是出方向模板（客户经理欢迎/系统提示）：%s",
                    sender, msg["text"][:60],
                )
                self._last_text_by_sender[sender] = msg["text"]
                continue

            # 读取条数：unread_n + 窗口内我方回复数 + 2 缓冲，上限 30
            unread_n = max(1, msg.get("unread_count", 1))
            now_ts = time.time()
            dq = self._recent_replies_by_sender.get(sender)
            if dq:
                while dq and now_ts - dq[0][0] > settings.echo_protect_seconds:
                    dq.popleft()
            bot_in_window = len(dq) if dq else 0
            read_n = min(unread_n + bot_in_window + 2, 30)

            all_msgs: list[str] = []
            if unread_n >= 2 or bot_in_window > 0:
                all_msgs = self.read_last_messages(
                    msg["conv_row"], read_n, expected_sender=sender,
                )
                if not all_msgs:
                    logger.warning(
                        "[%s] AX 读聊天面板失败，回退到预览处理（仅最后一条）",
                        sender,
                    )
            using_preview = not all_msgs  # 标记是否回退到了预览（WebArea 不可用）
            if not all_msgs:
                all_msgs = [msg["text"]]
                # 补充防回环：preview 模式下若文本是 bot 本会话曾发出的回复，
                # 几乎可以确定是自己的消息，跳过。
                # （echo_protect_seconds 到期 + _last_text_by_sender 被清空后的兜底）
                bot_ever_sent = self._bot_sent_texts.get(sender, set())
                if bot_ever_sent and all(m in bot_ever_sent for m in all_msgs):
                    logger.info(
                        "跳过 [%s]：preview '%s' 匹配 bot 会话历史回复，非新客户消息",
                        sender, all_msgs[0][:40],
                    )
                    self._last_text_by_sender[sender] = msg["text"]
                    continue

            # 防自回环：计数式过滤。dq 已于上面清理过期项。
            from collections import Counter as _C
            my_counts = _C(text for _, text in (dq or []))

            filtered_msgs: list[str] = []
            for m in reversed(all_msgs):
                if my_counts.get(m, 0) > 0:
                    my_counts[m] -= 1
                    continue
                filtered_msgs.append(m)
            filtered_msgs.reverse()

            # 若过滤后条数远超 unread_n（带入太多历史），截最后 unread_n 条
            if len(filtered_msgs) > unread_n * 2:
                filtered_msgs = filtered_msgs[-unread_n:]

            # 当被判为自回环时打印详情，便于排查
            if not filtered_msgs and all_msgs:
                my_text_set = [t for _, t in (dq or [])]
                logger.info(
                    "[%s] 读到 %d 条全被过滤（我方窗口内回复=%s）原文: %s",
                    sender, len(all_msgs),
                    my_text_set[-5:],  # 只显示最近 5 条
                    " | ".join(m[:30] for m in all_msgs),
                )
            if not filtered_msgs:
                # 最新预览恰好是我方刚发的，说明对方没新消息 → 跳过
                logger.info(
                    "跳过 [%s]：所读消息全部是我方最近发出的（防自回环）",
                    sender,
                )
                self._last_text_by_sender[sender] = msg["text"]
                continue
            all_msgs = filtered_msgs

            # 内容级去重：若这批客户消息与上次成功回复时完全相同，跳过
            content_batch = frozenset(all_msgs)
            if content_batch and content_batch == self._last_replied_batch.get(sender):
                logger.info(
                    "跳过 [%s]：消息内容与上次回复时完全相同（内容级去重）",
                    sender,
                )
                self._last_text_by_sender[sender] = msg["text"]
                continue

            combined_text = "\n".join(all_msgs)
            logger.info(
                "新消息 [%s] 共 %d 条: %s",
                sender, len(all_msgs), combined_text[:120].replace("\n", " | "),
            )

            # 构造历史对话：从 message_log 拉最近 5 条，转成 LLM 消息数组
            history: list[dict] = []
            try:
                past = message_log.get_by_sender(sender, limit=5)
                for row in past:
                    if row.message:
                        history.append({"role": "user", "content": row.message})
                    if row.reply:
                        history.append({"role": "assistant", "content": row.reply})
            except Exception as exc:
                logger.debug("读取历史对话失败：%s", exc)

            # 引擎用合并文本做匹配；LLM 收到上下文 + 历史
            result = engine.process_message(
                combined_text,
                sender_id=sender,
                context=all_msgs,
                history=history,
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

            # 打字风暴检测：delay 期间客户又发了消息 → 放弃本次回复，下轮重新合并
            new_unread = self._unread_count(msg["conv_row"])
            if new_unread > unread_n:
                logger.info(
                    "[%s] delay 期间收到 %d 条新消息，本轮跳过（下轮合并处理）",
                    sender, new_unread - unread_n,
                )
                continue

            t_start = time.monotonic()
            sent, used_method = self.send_reply(
                result["content"],
                conv_row=msg.get("conv_row"),
                expected_sender=sender,
            )
            latency_ms = int((time.monotonic() - t_start) * 1000)
            if sent:
                self._processed.add(msg["msg_hash"])
                # 内容级去重：记录本次回复的消息集合，下次见到相同集合直接跳过
                self._last_replied_batch[sender] = content_batch
                # 存 reply 而非客户消息：发送后企微 session 预览会更新为 bot 回复内容，
                # 下轮 extract_last_message 读到的预览是 bot 的回复，需与此对上才能去重。
                self._last_text_by_sender[sender] = result["content"]
                # 记录我方刚发出的回复，下一轮若 AX 读到相同文本应视为自己的消息
                from collections import deque as _dq
                dq = self._recent_replies_by_sender.setdefault(sender, _dq(maxlen=20))
                dq.append((time.time(), result["content"]))
                # 记录本会话所有已发出的回复文本，供 preview 模式下的补充防回环使用
                self._bot_sent_texts.setdefault(sender, set()).add(result["content"])
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


def _find_send_button(input_box, window):
    """
    找聊天输入框旁的"发送" AXButton。
    策略：从 input_box 向上最多 6 层找父节点，在每层的浅层子树里找 title='发送' 的按钮。
    找不到时回退到全窗口浅搜。
    """
    node = input_box
    for _ in range(6):
        try:
            parent = getattr(node, "AXParent", None)
            if parent is None:
                break
            for btn in _deep_find_all(parent, "AXButton", max_depth=3):
                try:
                    if str(getattr(btn, "AXTitle", "") or "").strip() == "发送":
                        return btn
                except Exception:
                    pass
            node = parent
        except Exception:
            break
    # 全窗口浅搜兜底
    for btn in _deep_find_all(window, "AXButton", max_depth=8):
        try:
            if str(getattr(btn, "AXTitle", "") or "").strip() == "发送":
                return btn
        except Exception:
            pass
    return None


def _deep_find_first_match(root, target, max_depth: int = 10) -> bool:
    """判断 target 是否在 root 的子树里（引用相等）。"""
    if max_depth < 0:
        return False
    try:
        if root is target:
            return True
    except Exception:
        pass
    for child in _safe_children(root):
        if _deep_find_first_match(child, target, max_depth - 1):
            return True
    return False


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


def _dump_ax_tree(root, filepath: str, max_depth: int = 8) -> None:
    """把 AX 树结构写入文件，用于调试。"""
    lines = []

    def _walk(node, depth):
        if depth > max_depth:
            return
        indent = "  " * depth
        try:
            role = str(getattr(node, "AXRole", "") or "")
            title = str(getattr(node, "AXTitle", "") or "")[:40]
            desc = str(getattr(node, "AXDescription", "") or "")[:40]
            value = str(getattr(node, "AXValue", "") or "")[:60]
            label = str(getattr(node, "AXRoleDescription", "") or "")[:30]
            lines.append(f"{indent}{role}  title={title!r}  desc={desc!r}  value={value!r}  label={label!r}")
        except Exception as e:
            lines.append(f"{indent}[err: {e}]")
            return
        for child in _safe_children(node):
            _walk(child, depth + 1)

    _walk(root, 0)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("AX 树已写入 %s（共 %d 行）", filepath, len(lines))
    except Exception as e:
        logger.warning("写 AX 树失败：%s", e)


def _dismiss_function_panel(window, panel_names: list[str] | None = None) -> bool:
    """
    通过 AXCheckBox title='展开' 折叠企微功能浮层面板（经营大厅等）。
    该控件位于快速会议按钮右侧，点击可收起/展开右侧功能面板。
    """
    for node in _deep_find_all(window, "AXCheckBox", max_depth=10):
        try:
            title = str(getattr(node, "AXTitle", "") or "").strip()
            if title == "展开":
                for action in ("Press", "AXPress"):
                    fn = getattr(node, action, None)
                    if callable(fn):
                        try:
                            fn()
                            logger.info("已通过 AXCheckBox('展开') 折叠功能浮层")
                            return True
                        except Exception as exc:
                            logger.debug("AXCheckBox Press 失败：%s", exc)
        except Exception:
            continue
    return False


def _read_chat_from_table(window, count: int) -> list[str]:
    """
    当 AXWebArea 被功能浮层遮挡时，从右侧面板的 AXTable 直接读聊天消息。
    只扫浅层（depth=4），避免全树 DFS 超时。
    """
    UI_NOISE = {
        "@微信", "筛选", "搜索", "共", "条", "批量处理",
        "经营大厅", "快捷回复", "人工客服", "快速会议",
        "展开", "收起", "发送", "取消",
    }
    UI_NOISE_PREFIX = ("您不是该客户绑定", "对方默认同意存档")

    # 找所有 AXTable（浅扫），跳过第一个（左侧会话列表）
    tables = _deep_find_all(window, "AXTable", max_depth=6)
    if len(tables) < 2:
        logger.debug("_read_chat_from_table: 找到 %d 个 AXTable，不足 2 个", len(tables))
        return []

    for table in tables[1:]:
        texts_raw = _deep_find_all(table, "AXStaticText", max_depth=5)
        values = []
        for t in texts_raw:
            v = str(getattr(t, "AXValue", "") or "").strip()
            if not v or not _is_message_text(v):
                continue
            if v in UI_NOISE or any(v.startswith(p) for p in UI_NOISE_PREFIX):
                continue
            values.append(v)
        if values:
            logger.info("从 AXTable 读到 %d 条消息（绕过功能浮层）", len(values))
            return values[-count:]

    logger.debug("_read_chat_from_table: 所有 AXTable 均无有效消息")
    return []


def _parse_lead_message(text: str) -> tuple[str, str | None]:
    """解析经营线索消息文本，返回 (旅客名, 微信昵称)。

    消息格式（示例）：
        【出行提醒】
        旅客:郝祥宇
        微信昵称:
        航程信息:深圳宝安机场-武汉天河机场

        线索详情>>

    返回：
        traveler: 旅客名（如 '郝祥宇'），找不到返回 ''
        qiwei_nick: 微信昵称的值——非空字符串 = 已添加企微；
                    空字符串 ''       = 字段存在但值为空（未添加企微）；
                    None             = 字段不存在（格式异常）
    """
    if not text:
        return "", None
    traveler = ""
    qiwei: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("旅客:") or s.startswith("旅客："):
            traveler = s.split(":", 1)[1] if ":" in s else s.split("：", 1)[1]
            traveler = traveler.strip()
        elif s.startswith("微信昵称:") or s.startswith("微信昵称："):
            v = s.split(":", 1)[1] if ":" in s else s.split("：", 1)[1]
            qiwei = v.strip()
    return traveler, qiwei


def _force_click_conv_row(row, pid) -> bool:
    """PostToPid 鼠标点击会话行中心，强制让 Chromium 切换可见聊天面板。

    与 _press_conv_row（仅 AX 操作）不同，这里用真实鼠标事件触发 Chromium
    内部的会话切换，能突破 AXSelected = True 兜底"只选中不切换面板"的死局。
    PostToPid 定向投递事件给指定 pid，不让该 app 进入前台/抢焦点。
    """
    try:
        import Quartz
        pos = getattr(row, "AXPosition", None)
        sz  = getattr(row, "AXSize", None)
        if not pid or not pos or not sz:
            return False
        cx = pos[0] + sz[0] / 2
        cy = pos[1] + sz[1] / 2
        pt = Quartz.CGPointMake(cx, cy)
        for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPostToPid(pid, ev)
            time.sleep(0.05)
        logger.info("PostToPid 强制点击会话行中心 pid=%s (%.0f, %.0f)", pid, cx, cy)
        return True
    except Exception as exc:
        logger.warning("PostToPid 点击会话行失败：%s", exc)
        return False


def _press_conv_row(row) -> bool:
    """
    点击会话行以切换到该会话。**只用 AX 级操作，不使用鼠标事件**以避免抢焦点。
    尝试顺序：row.Press/AXPress → AXCell.Press/AXPress → row.AXSelected = True。
    """
    # 1. AXRow 自己 / 内部 AXCell 的 Press 动作
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

    # 2. AXSelected 赋值（不切换可见面板，但不抢焦点；配合 send_reply 的 PostToPid
    #    对输入框直接操作依然能发送成功）
    try:
        row.AXSelected = True
        logger.debug("通过 AXSelected=True 切换会话成功（可能未切换可见面板）")
        return True
    except Exception as exc:
        logger.debug("AXSelected 赋值失败：%s", exc)

    # 3. 彻底失败（不使用 Quartz 鼠标点击，避免抢焦点）
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
    过滤掉时间戳、相对时间标签、空字符串等非消息内容。
    时间戳示例："12:30"、"昨天 18:00"、"2024-01-01"
    相对时间示例："刚刚"、"1分钟前"、"2小时前"、"昨天"、"前天"
    """
    if not text:
        return False
    import re
    # 过滤时间/日期格式（必须像真实时间戳：含冒号的时间、含分隔符的日期）
    # 避免把 "1"、"42" 等纯数字短消息误当时间过滤
    if re.fullmatch(
        r"\d{1,2}:\d{2}"                        # 12:30
        r"|\d{2,4}[-/]\d{1,2}([-/]\d{1,2})?"   # 04-14 / 2024-04-14
        r"|\d{4}年\d{1,2}月(\d{1,2}日)?",       # 2024年4月14日
        text,
    ):
        return False
    # 过滤相对时间标签
    if re.fullmatch(r"刚刚|\d+分钟前|\d+小时前|昨天|前天|星期[一二三四五六日]", text):
        return False
    return True

from __future__ import annotations

"""
企业微信内置 AI 助手面板：用热键唤出、附加当前客户完整聊天记录为上下文、
拿到面板自己读聊天记录生成的回复文本。

实测结论（2026-07-07 手动+脚本验证）：
- 热键 Control+Command+Option+Space 是全局快捷键，必须用
  Quartz.CGEventPost(kCGHIDEventTap, ...) 发到系统级事件流，PostToPid 无效
  （面板不是 WeCom 窗口内部快捷键，是系统级注册的）。
- 面板是独立的 AXWindow（AXTitle==''），跟主窗口（AXTitle=='企业微信'）分开。
- 不给"添加 → 和 XX 的聊天"这个 context chip，AI 有时读不到聊天记录，
  会反问"对方说了什么"——必须显式点这个 chip 附加完整聊天记录，
  不能只在 prompt 里手打客户原话（違反"必须让它自己读"的要求）。
- 面板关闭后再打开是全新会话（不带之前的历史），所以每次生成完都应关闭，
  避免下一个客户复用到上一个客户的对话历史。
"""

import logging
import re
import time
from typing import NamedTuple

import Quartz

from reply.customer_address import address_instruction

logger = logging.getLogger(__name__)

_SPACE_KEYCODE = 49
_HOTKEY_FLAGS = (
    Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskAlternate
)

# 内部标记：AI 判断"这条消息需要转人工处理"时，附加在回复文本末尾。
# 只用于程序内部识别——发给客户前一定会被 generate_reply 强制去掉，客户看不到。
MANUAL_FLAG_MARKER = "[[NEEDHUMAN]]"

# 用明确的定界符包住"真正要发给客户的正文"，程序按定界符提取——不能只靠
# "不要输出分析/说明"这种口头约束，实测 AI 有时会把判断过程也写进回复正文里
# （比如"选座属于需人工处理的事项，我在回复末尾加上内部标记"），一旦真发给
# 客户就穿帮了。用定界符能保证不管 AI 输出多少额外的分析文字，只提取标记
# 之间的内容当正文，其余一律丢弃。
_REPLY_START = "===回复正文开始==="
_REPLY_END = "===回复正文结束==="
_NO_INBOUND = "===无客户消息==="

_ASK_PROMPT_TEMPLATE = (
    "阅读这个客户的聊天记录，先辨别最后一条消息是谁发出的。若最近一条消息是我方发出、"
    "客户在这之后没有新消息，只输出 ===无客户消息===（不写回复正文，也不要回答我方的群发、表情或安抚消息）。"
    "只有确认最近一条消息来自客户时，才针对那条客户消息拟客服回复。\n"
    f"确认有客户新消息时，必须用下面这个格式输出：\n{_REPLY_START}\n<这里只写发给客户看的回复正文>\n{_REPLY_END}\n\n"
    f"{_REPLY_START} 到 {_REPLY_END} 之间只能是纯客服话术本身——不能包含任何"
    "你的思考过程、判断依据、方案编号、说明、分析、备注、markdown 标记，"
    "也不能提到'标记''内部''系统识别''需人工处理'这类字眼来解释你在做什么，"
    "这两个定界符之外你想怎么分析都行，但里面必须是干净到可以直接发出去的文本。\n\n"
    "正文要求：严禁使用'我是您的XX客户经理/客服/助理[人名]'这类自我介绍开场白，"
    "也不要用'第一时间回复''有事请留言''工作日9:00-12:00...非工作时间请...'"
    "这类工时/自动回复模板措辞——这类话术会被公司考核系统识别为'自动回复'，"
    "即使真的回复了客户也会被判定成未回复、扣客户经理的考核分。"
    "直接像人一样针对客户的具体问题自然作答即可，不用先自报身份。\n\n"
    "重要：这是应答式客服，不是主动营销/引导式服务。只回应客户实际说的/问的"
    "内容，绝对不要主动提出帮客户查里程、推荐兑换机票、推荐活动、建议客户去"
    "做什么等对方没有要求的事情——这类主动招揽表面上热情，实际上一旦客户真"
    "答应了，后续都要客户经理去跟进处理，等于凭空给客户经理增加工作量。"
    "如果客户只是打招呼、致谢、简单确认（没有具体问题或需求），就礼貌简短"
    "回应就行，不要在后面加'需要帮忙随时说''有什么想问的吗'这类开放式反问"
    "或主动揽活的话。\n\n"
    "如果客户最新这条消息是语音消息（聊天记录里显示为'[语音]'）、语音通话、"
    "视频通话邀请这类你没法处理的形式，自然地告诉客户你们这边设备暂时"
    "听不了语音/接不了通话，麻烦他打字说一下就行——用你自己的话自然表达，"
    "不用照抄某个固定说法，但意思要到位。\n\n"
    "如果客户这条消息涉及选座/换座、改签、退票、升舱、值机、查/兑里程、"
    "开发票、修改证件姓名或手机号信息、取消订单——这些你实际上没法代为操作、"
    f"必须由人工完成的事项，在 {_REPLY_END} 这一行之后再另起一行，只写"
    f"{MANUAL_FLAG_MARKER}（不要加任何解释）；不涉及这些事项就不要写这一行。"
)


def build_ask_prompt(contact_sender: str) -> str:
    """Keep the existing context-chip request, with a contact-specific address rule."""
    return _ASK_PROMPT_TEMPLATE + "\n\n【客户称呼（优先级最高）】" + address_instruction(contact_sender)


class NativeAIResult(NamedTuple):
    status: str  # reply | no_inbound | failed
    reply: str | None = None
    needs_manual: bool = False


def classify_response(raw: str) -> NativeAIResult:
    """Distinguish no inbound message from a malformed/failed AI reply."""
    if raw.strip() == _NO_INBOUND:
        return NativeAIResult("no_inbound")
    # A correctly delimited reply wins over incidental mentions of the same words
    # inside the customer's question or the AI's answer.
    if _REPLY_START in raw and _REPLY_END in raw:
        clean = _extract_final_reply(raw)
        if clean:
            return NativeAIResult("reply", clean.replace(MANUAL_FLAG_MARKER, "").strip(), MANUAL_FLAG_MARKER in raw)
    # Older WeCom replies can explain the no-inbound finding in prose instead of
    # following the requested marker. Require both absence of inbound and evidence
    # that recent messages were sent by us; don't treat arbitrary prose as a verdict.
    no_inbound = re.search(r"(?:没有|并没有|未).{0,15}来自.{0,50}的消息|(?:对方|客户).{0,15}(?:没有|没).{0,10}发.{0,5}消息", raw)
    our_messages = re.search(r"(?:最近|最新).{0,30}(?:消息|条).{0,20}(?:都是|均为|是)(?:你|您|我方)发出|(?:最近|最新).{0,30}(?:你|您|我方)发出", raw)
    if no_inbound and our_messages:
        return NativeAIResult("no_inbound")
    _extract_final_reply(raw)  # log malformed output for diagnostics
    return NativeAIResult("failed")


def _extract_final_reply(raw: str) -> str | None:
    """按定界符提取真正要发给客户的正文，丢弃 AI 写在外面的分析/说明文字。

    定界符缺失（AI 没按格式来）时返回 None，判定为本次生成失败——绝不能把
    未经清洗的原始文本当兜底发出去。2026-07-07 生产事故：客户马英枰只回了句
    "了解，谢谢啦"，AI 没走定界符格式，直接输出了一句语无伦次的追问
    （"您说的'这个客户'具体是指哪位客户？"），当时的兜底逻辑是退回整段原文，
    结果就把这句话发给了真实客户。调用方应把 None 当生成失败处理，回退到
    原 LLM 链路，而不是冒险发送未经格式约束的原始输出。
    """
    import re
    m = re.search(
        re.escape(_REPLY_START) + r"\s*\n?(.*?)\n?\s*" + re.escape(_REPLY_END),
        raw, re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    logger.warning(
        "AI 回复未按定界符格式输出，判定生成失败（不发送未清洗的原始文本）：%s",
        raw[:200],
    )
    return None


def _safe_children(el) -> list:
    try:
        return list(el.AXChildren or [])
    except Exception:
        return []


def _find_all(root, role: str, max_depth: int = 12) -> list:
    out = []
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            out.append(root)
    except Exception:
        return out
    if max_depth <= 0:
        return out
    for child in _safe_children(root):
        out.extend(_find_all(child, role, max_depth - 1))
    return out


def _find_first(root, role: str, max_depth: int = 12):
    hits = _find_all(root, role, max_depth)
    return hits[0] if hits else None



# macOS 修饰键 keycode，用于热键发送后显式"松开"，防止残留修饰键状态。
_MODIFIER_KEYCODES = (59, 55, 58)  # Control, Command, Option


def _send_hotkey() -> None:
    """发送全局热键 Control+Command+Option+Space（唤出/收起 AI 面板）。

    必须用 kCGHIDEventTap（全局事件流），PostToPid 发到 WeCom 进程测试无效——
    这是系统级注册的快捷键，不是 WeCom 窗口内部处理的。

    2026-07-07 排查：发这个热键（用 CGEventSetFlags 给 Space 按键事件打
    Control+Command+Option 标记，但从没发过这三个修饰键本身的 keyDown/
    keyUp）之后，偶尔会观察到后续点击会话行变成了右键菜单（Control+点击
    在 macOS 上等价于右键）——多次复现，且发生在企微重启后依然存在，
    排除了"面板卡死"的可能，怀疑是残留的修饰键状态。这里加一步防御：
    热键发完后显式发一遍这三个修饰键的 keyUp，确保系统里不留"被按住"的
    修饰键状态，即使这不是 100% 确认的根因，做这层防御成本很低。
    """
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, _SPACE_KEYCODE, down)
        Quartz.CGEventSetFlags(ev, _HOTKEY_FLAGS)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.05)

    for keycode in _MODIFIER_KEYCODES:
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)  # keyUp
        Quartz.CGEventSetFlags(ev, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    time.sleep(0.05)


def _panel_window(app):
    """返回当前已打开的 AI 面板窗口（AXTitle != '企业微信'），没打开返回 None。"""
    try:
        windows = app.AXWindows
    except Exception:
        return None
    for w in windows:
        title = str(getattr(w, "AXTitle", "") or "")
        if title != "企业微信":
            return w
    return None


def open_panel(app, timeout: float = 4.0):
    """确保面板打开并返回其窗口引用。已经开着则直接复用（不重复触发热键，
    重复触发是「收起」，会把面板关掉）。"""
    existing = _panel_window(app)
    if existing is not None:
        return existing

    _send_hotkey()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.2)
        w = _panel_window(app)
        if w is not None:
            return w
    return None


def _find_new_conversation_button(panel):
    """定位面板左上角"开启新对话"按钮（⊕图标）。

    实测：面板顶部有一排 5 个无 title/desc/help 文本的 AXButton（3 个圆点 +
    ⧉ 布局切换 + ⊕ 新对话），⊕ 是这一排里 x 坐标最大的那个，且都在窗口顶部
    40px 以内（用 AXPosition 相对面板窗口原点的 y 偏移筛出这一排）。
    """
    panel_pos = getattr(panel, "AXPosition", None)
    if not panel_pos:
        return None
    top_y = panel_pos[1]
    candidates = []
    for btn in _find_all(panel, "AXButton", max_depth=6):
        bpos = getattr(btn, "AXPosition", None)
        if not bpos:
            continue
        if 0 <= bpos[1] - top_y < 40:
            candidates.append((bpos[0], btn))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def start_new_conversation(panel) -> bool:
    """点击"开启新对话"，重置面板会话（清空历史 + 让"和 XX 的聊天"这个
    context chip 重新可用）。实测面板"关闭再打开"(热键 toggle)只是隐藏/
    显示，并不会真正清空历史，导致：
    1) 跨客户复用同一个面板会话时，AI 可能读到上一个客户的对话历史；
    2) chip 只在"第一次"提议时出现，用过一次后同一会话里不会再推荐，
       如果这时候切到了新客户，反而可能拿不到新客户的聊天记录 chip。
    每次 generate_reply 一开始就点这个按钮，从根源上解决这两个问题。
    """
    btn = _find_new_conversation_button(panel)
    if btn is None:
        logger.warning("找不到'开启新对话'按钮，跳过重置（可能导致历史跨客户累积）")
        return False
    for action in ("Press", "AXPress"):
        fn = getattr(btn, action, None)
        if callable(fn):
            try:
                fn()
                logger.info("已点击'开启新对话'，面板会话已重置")
                return True
            except Exception as exc:
                logger.debug("点击'开启新对话'失败（%s）：%s", action, exc)
    return False


def close_panel(app, attempts: int = 3, settle: float = 1.2) -> bool:
    """收起面板（仅当确实开着时才发热键，避免误触发把它重新打开）。

    实测有时一次热键切换不生效（面板正忙/焦点未让出），重试几次。收不掉不算
    致命错误——不影响下次 generate_reply（open_panel 会直接复用现开的面板），
    只是历史会跨轮累积，值多得留意但不阻塞主流程。
    """
    try:
        for _ in range(attempts):
            if _panel_window(app) is None:
                return True
            _send_hotkey()
            time.sleep(settle)
        still_open = _panel_window(app) is not None
        if still_open:
            logger.debug("close_panel：%d 次尝试后面板仍开着", attempts)
        return not still_open
    except Exception as exc:
        logger.debug("close_panel 异常：%s", exc)
        return False


def _find_context_chip_button(panel):
    """在面板里找"和 XX 的聊天"这个 context chip 对应的 AXButton
    （chip 的 AXStaticText 后面紧跟的同层 sibling）。找不到返回 None。

    2026-07-07 教训：**不要求 chip 文字包含客户的备注名**。chip 里的"XX"
    用的是对方的微信号/微信昵称，跟我们 sender_id 里用的备注名（比如
    "刘明瑞(男)-3647"）经常对不上——同一个客户，chip 显示"和marionliew的
    聊天"（这是他真实微信号），备注名却是"刘明瑞"，两者本就是不同的东西，
    不是 bug。之前用"contact_core in val"做匹配，遇到备注名和微信号不同的
    客户就永远匹配不上、误判成"附加失败"。改成只要求"和...的聊天"这个
    固定模式，不管中间具体是什么名字——反正面板里同时只会有一个这种
    chip（跟"当前屏幕: ..."那个格式不同，不会混淆）。
    """
    target_suffix = "的聊天"

    def _walk(el):
        children = _safe_children(el)
        for i, c in enumerate(children):
            role = str(getattr(c, "AXRole", "") or "")
            if role == "AXStaticText":
                val = str(getattr(c, "AXValue", "") or "").strip()
                if val.startswith("和") and val.endswith(target_suffix):
                    if i + 1 < len(children):
                        nxt = children[i + 1]
                        if str(getattr(nxt, "AXRole", "") or "") == "AXButton":
                            return nxt
            found = _walk(c)
            if found is not None:
                return found
        return None

    return _walk(panel)


def attach_contact_context(
    panel, contact_core: str, retries: int = 5, retry_interval: float = 0.5,
) -> bool:
    """点击"添加 → 和 {contact_core} 的聊天"这个 context chip，附加完整聊天记录。

    generate_reply 每次都先点"开启新对话"重置会话，所以这里**必须**能找到
    chip 才算成功——不再有"同会话已经附加过、这次不会再推荐"的例外情况
    （那个例外只在"面板沿用旧会话"时成立，现在每次都是新会话）。

    2026-07-07 生产事故：旧版本把"找不到 chip"当成"已经附加过"直接放行，
    结果面板其实没拿到聊天记录，AI 瞎猜答不上来，反问"您说的这个客户具体
    是指哪位"，这句话被发给了真实客户。现在找不到 chip 就是真失败，重试
    几次仍找不到就返回 False，调用方应放弃这次生成、回退原 LLM 链路，
    绝不能在没有聊天记录上下文的情况下让 AI 瞎生成。
    """
    if not contact_core:
        return False

    btn = None
    for attempt in range(retries):
        btn = _find_context_chip_button(panel)
        if btn is not None:
            break
        time.sleep(retry_interval)
    if btn is None:
        logger.warning(
            "重试 %d 次仍未找到'和 XX 的聊天' context chip（目标客户备注=%s），判定附加失败",
            retries, contact_core,
        )
        return False

    for action in ("Press", "AXPress"):
        fn = getattr(btn, action, None)
        if callable(fn):
            try:
                fn()
                logger.info("已附加聊天记录 context（目标客户备注=%s）", contact_core)
                return True
            except Exception as exc:
                logger.debug("按 chip 按钮失败（%s）：%s", action, exc)
    return False


def _find_input_textarea(panel):
    """定位面板底部的 prompt 输入框（跟历史对话表格的 AXTextArea 分开的那个）。

    面板顶层结构：AXWindow > AXSplitGroup > [历史 AXScrollArea(内含AXTable), ...
    chips..., 输入 AXScrollArea(内含AXTextArea), 发送等 AXButton...]
    输入框所在 AXScrollArea 是 split group 直接子节点里最后一个 AXScrollArea。
    """
    split = None
    for c in _safe_children(panel):
        if str(getattr(c, "AXRole", "") or "") == "AXSplitGroup":
            split = c
            break
    if split is None:
        return None
    scrolls = [c for c in _safe_children(split) if str(getattr(c, "AXRole", "") or "") == "AXScrollArea"]
    if not scrolls:
        return None
    input_scroll = scrolls[-1]
    tareas = _find_all(input_scroll, "AXTextArea", max_depth=4)
    return tareas[0] if tareas else None


def _find_send_button(panel):
    """定位发送按钮：split group 直接子节点里，紧跟在输入 AXScrollArea 后面的
    第一个 AXButton（实测：输入框(AXScrollArea) → 发送(AXButton) → +号(AXButton) → ...）。
    """
    split = None
    for c in _safe_children(panel):
        if str(getattr(c, "AXRole", "") or "") == "AXSplitGroup":
            split = c
            break
    if split is None:
        return None
    children = _safe_children(split)
    scroll_idx = None
    for i, c in enumerate(children):
        if str(getattr(c, "AXRole", "") or "") == "AXScrollArea":
            scroll_idx = i  # 循环到底剩最后一个即输入框所在的 AXScrollArea
    if scroll_idx is None:
        return None
    for c in children[scroll_idx + 1:]:
        if str(getattr(c, "AXRole", "") or "") == "AXButton":
            return c
    return None


def _extract_last_reply(panel, submitted_prompt: str) -> str | None:
    """按内容精确定位"我刚提交的这条 prompt"所在行，取它后面一行的回复文本。

    不能简单取 AXTable 最后一行——面板历史累积多轮后可能被 Chromium 虚拟化/
    回收 DOM 节点，"最后一个 AXRow 子节点"不一定是本轮真正的最新回复。
    用 prompt 原文精确匹配定位，再看下一行是否已标记"已完成"，更抗这种情况。
    """
    table = _find_first(panel, "AXTable")
    if table is None:
        return None
    rows = [r for r in _safe_children(table) if str(getattr(r, "AXRole", "") or "") == "AXRow"]
    prompt_stripped = submitted_prompt.strip()
    for i, row in enumerate(rows):
        vals = [
            str(getattr(t, "AXValue", "") or "").replace("￼", "").strip()
            for t in _find_all(row, "AXTextArea")
        ]
        if not any(v == prompt_stripped for v in vals):
            continue
        if i + 1 >= len(rows):
            return None  # 提交的这一行还没有对应的回复行出现
        reply_row = rows[i + 1]
        statuses = [str(getattr(t, "AXValue", "") or "").strip() for t in _find_all(reply_row, "AXStaticText")]
        if "已完成" not in statuses:
            return None  # 回复行已出现但还没生成完
        for t in _find_all(reply_row, "AXTextArea"):
            v = str(getattr(t, "AXValue", "") or "").strip()
            if v:
                return v
        return None
    return None


def ask(panel, prompt: str, pid: int, timeout: float = 30.0, poll_interval: float = 0.5) -> str | None:
    """把 prompt 写进面板输入框、回车提交，轮询直到该轮标记'已完成'，返回回复文本。

    关键：attach_contact_context 点击 chip 后，chip 会变成输入框里的一个内嵌
    占位符（AXValue 里的 '￼' 字符）。这里必须在现有内容后面追加 prompt，
    不能整体覆盖 AXValue——覆盖会把刚附加的聊天记录引用一起冲掉，等于白附加。
    """
    input_box = None
    for attempt in range(5):
        input_box = _find_input_textarea(panel)
        if input_box is not None:
            break
        time.sleep(0.4)
    if input_box is None:
        logger.warning("找不到 AI 面板输入框（重试 5 次后仍失败）")
        return None
    try:
        existing = str(getattr(input_box, "AXValue", "") or "")
    except Exception:
        existing = ""
    full_value = f"{existing}\n{prompt}" if existing.strip() else prompt
    try:
        input_box.AXValue = full_value
    except Exception as exc:
        logger.warning("写入 AI 面板 prompt 失败：%s", exc)
        return None

    time.sleep(0.2)

    # 优先按发送按钮提交（AXPress 比模拟 Return 键更可靠——实测 PostToPid 回车
    # 有时不生效，按钮 AXPress 每次都成功清空输入框）；找不到按钮才退回模拟回车。
    submitted = False
    send_btn = _find_send_button(panel)
    if send_btn is not None:
        for action in ("Press", "AXPress"):
            fn = getattr(send_btn, action, None)
            if callable(fn):
                try:
                    fn()
                    submitted = True
                    break
                except Exception as exc:
                    logger.debug("按发送按钮失败（%s）：%s", action, exc)
    if not submitted:
        logger.debug("找不到发送按钮，退回模拟回车键提交")
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 36, down)  # 36 = kVK_Return
            Quartz.CGEventPostToPid(pid, ev)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        reply = _extract_last_reply(panel, prompt)
        if reply:
            return reply
    logger.warning("AI 面板 %.0fs 内未返回结果（超时）", timeout)
    return None


def generate_reply(
    app, pid: int, contact_sender: str, timeout: float = 30.0,
) -> NativeAIResult:
    """Complete panel flow; return reply, no_inbound, or failed explicitly.

    A no_inbound verdict is authoritative: the caller must not invoke fallback AI.
    """
    contact_core = contact_sender.split("(")[0].strip() if contact_sender else ""
    try:
        panel = open_panel(app)
        if panel is None:
            logger.warning("打开企微 AI 面板失败（热键未生效或超时）")
            return NativeAIResult("failed")
        try:
            # 每次都先开新对话：保证历史不跨客户累积、且"和 XX 的聊天"
            # 这个 chip 每次都会重新可用（chip 只在新会话第一次提议）。
            start_new_conversation(panel)
            time.sleep(1.0)
            if not attach_contact_context(panel, contact_core):
                return NativeAIResult("failed")
            time.sleep(0.3)
            raw = ask(panel, build_ask_prompt(contact_sender), pid, timeout=timeout)
            if raw is None:
                return NativeAIResult("failed")
            outcome = classify_response(raw)
            if outcome.status == "no_inbound":
                logger.info("企微 AI 判断该会话最近没有客户新消息，不触发二级 AI [%s]", contact_sender)
            elif outcome.needs_manual:
                logger.info("企微 AI 判断这条需转人工处理（已从回复中剔除内部标记）")
            return outcome
        finally:
            close_panel(app)
    except Exception as exc:
        logger.exception("企微内置 AI 生成回复异常：%s", exc)
        try:
            close_panel(app)
        except Exception:
            pass
        return NativeAIResult("failed")

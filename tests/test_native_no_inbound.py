"""Native WeCom AI's no-inbound verdict must never become fallback LLM input."""
from unittest.mock import patch

from config import Settings
from wecom import ai_panel
from wecom.mac_watcher import WeChatWatcher


def _run_one_tick(native_outcome, quick_rule=None):
    watcher = object.__new__(WeChatWatcher)
    watcher._last_text_by_sender = {}
    watcher._recent_replies_by_sender = {}
    watcher._bot_sent_texts = {}
    watcher._last_replied_batch = {}
    watcher._processed = set()
    message = {"sender_id": "张银娜(女)-2739", "text": "群发中秋祝福", "has_wechat_tag": True,
               "conv_row": object(), "msg_hash": "outgoing-broadcast"}
    with patch.object(Settings, "is_work_time", return_value=True), \
         patch.object(WeChatWatcher, "_get_app", return_value=object()), \
         patch.object(WeChatWatcher, "find_unread_conversations", return_value=[message["conv_row"]]), \
         patch.object(WeChatWatcher, "extract_last_message", return_value=message), \
         patch.object(WeChatWatcher, "_unread_count", return_value=1), \
         patch.object(WeChatWatcher, "_is_outgoing_template", return_value=False), \
         patch.object(WeChatWatcher, "_close_xiansuo_popups"), \
         patch("wecom.mac_watcher.settings.wecom_ai_enabled", True), \
         patch("wecom.mac_watcher.rules.match", return_value=quick_rule), \
         patch("wecom.mac_watcher.ai_panel.generate_reply", return_value=native_outcome) as native, \
         patch("wecom.mac_watcher.engine.process_message") as fallback, \
         patch.object(WeChatWatcher, "send_reply") as send:
        watcher.tick()
    return watcher, native, fallback, send


def test_no_inbound_never_reaches_rules_fallback_or_send():
    watcher, native, fallback, send = _run_one_tick(ai_panel.NativeAIResult("no_inbound"), quick_rule="收到")
    native.assert_called_once()
    fallback.assert_not_called()
    send.assert_not_called()
    assert "outgoing-broadcast" in watcher._processed


def test_native_failure_does_not_guess_from_undirected_preview():
    _, native, fallback, send = _run_one_tick(ai_panel.NativeAIResult("failed"))
    native.assert_called_once()
    fallback.assert_not_called()
    send.assert_not_called()


AGNA_RESPONSE = """我查看了你与 AgNa 的聊天记录，发现最近一段时间内并没有来自 AgNa 的消息——最近的两条消息都是你发出的：
1. 2026-09-24 — 你发的中秋祝福
2. 2026-09-28 10:54 — 你发的玫瑰 emoji
也就是说，AgNa 最近没有给你发过消息，因此没有需要应答的内容。"""


def test_real_ag_na_response_is_not_fallback_failure():
    with patch.object(ai_panel, "open_panel", return_value=object()), \
         patch.object(ai_panel, "start_new_conversation", return_value=True), \
         patch.object(ai_panel, "attach_contact_context", return_value=True), \
         patch.object(ai_panel, "ask", return_value=AGNA_RESPONSE), \
         patch.object(ai_panel, "close_panel"), \
         patch.object(ai_panel.time, "sleep"):
        outcome = ai_panel.generate_reply(None, 0, "张银娜(女)-2739")
    assert outcome.status == "no_inbound"
    assert outcome.reply is None


def test_explicit_no_inbound_marker_is_authoritative():
    assert ai_panel.classify_response("===无客户消息===").status == "no_inbound"


def test_unformatted_other_output_is_still_failure():
    assert ai_panel.classify_response("您说的这个客户具体是哪位？").status == "failed"


def test_formatted_reply_is_success():
    outcome = ai_panel.classify_response("===回复正文开始===\n周先生您好\n===回复正文结束===")
    assert outcome.status == "reply"
    assert outcome.reply == "周先生您好"


def test_formatted_customer_quote_is_not_mistaken_for_no_inbound():
    raw = "===回复正文开始===\n您说最近没有来自航司的消息，最近两条消息都是你发出的，我帮您核实。\n===回复正文结束==="
    assert ai_panel.classify_response(raw).status == "reply"

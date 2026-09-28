"""Native WeCom AI's no-inbound verdict must never become fallback LLM input."""
from unittest.mock import patch

from config import Settings
from wecom import ai_panel
from wecom.mac_watcher import WeChatWatcher


def _run_one_tick(native_outcome=None, local_messages=None, local_directional=None):
    from unittest.mock import MagicMock
    watcher = object.__new__(WeChatWatcher)
    watcher._last_text_by_sender = {}
    watcher._recent_replies_by_sender = {}
    watcher._bot_sent_texts = {}
    watcher._last_replied_batch = {}
    watcher._processed = set()
    watcher._daily = MagicMock()
    watcher._last_panel_directional = ("张银娜(女)-2739", local_directional) if local_directional is not None else None
    message = {"sender_id": "张银娜(女)-2739", "text": "群发中秋祝福", "has_wechat_tag": True,
               "conv_row": object(), "msg_hash": "outgoing-broadcast"}
    with patch.object(Settings, "is_work_time", return_value=True), \
         patch.object(WeChatWatcher, "_get_app", return_value=object()), \
         patch.object(WeChatWatcher, "find_unread_conversations", return_value=[message["conv_row"]]), \
         patch.object(WeChatWatcher, "extract_last_message", return_value=message), \
         patch.object(WeChatWatcher, "_unread_count", return_value=1), \
         patch.object(WeChatWatcher, "_is_outgoing_template", return_value=False), \
         patch.object(WeChatWatcher, "read_last_messages", return_value=local_messages or []), \
         patch.object(WeChatWatcher, "_harvest_corrections"), \
         patch.object(WeChatWatcher, "_close_xiansuo_popups"), \
         patch("wecom.mac_watcher.settings.wecom_ai_enabled", True), \
         patch("wecom.mac_watcher.message_log"), \
         patch("wecom.mac_watcher.ai_panel.generate_reply", return_value=native_outcome) as native, \
         patch("wecom.mac_watcher.engine.process_message") as fallback, \
         patch.object(WeChatWatcher, "send_reply", return_value=(True, "mocked")) as send:
        watcher.tick()
    return watcher, native, fallback, send


def test_local_all_out_messages_skip_without_launching_panel():
    watcher, native, fallback, send = _run_one_tick(
        ai_panel.NativeAIResult("reply", "这条不该发"),
        local_directional=[{"side": "out", "text": "中秋祝福"}],
    )
    native.assert_not_called()
    fallback.assert_not_called()
    send.assert_not_called()
    assert "outgoing-broadcast" in watcher._processed


def test_local_inbound_still_goes_through_panel_and_sends():
    watcher, native, fallback, send = _run_one_tick(
        ai_panel.NativeAIResult("reply", "周先生您好"),
        local_messages=["客户您好"],
        local_directional=[{"side": "in", "text": "客户您好"}],
    )
    native.assert_called_once()
    fallback.assert_not_called()
    send.assert_called_once()
    assert send.call_args[0][0] == "周先生您好"


def test_no_inbound_never_reaches_fallback_or_send():
    watcher, native, fallback, send = _run_one_tick(ai_panel.NativeAIResult("no_inbound"))
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


def test_real_production_no_inbound_phrasings():
    """Live phrasing from today's daemon.log: prose verdicts, no delimiter."""
    samples = [
        "根据聊天记录，最近一条消息是 2026-09-24 17:55:54 由我方（南航高端客户经理-小明(刘明瑞)）发出的中秋祝福，此后客户没有新消息。",
        "我已阅读完聊天记录。最后一条消息是2026-09-24 17:55:54我方发出的中秋祝福，客户在此之后没有新消息。",
        "根据聊天记录，最近一条消息是 2026-09-24 17:55:54 我方（南航高端客户经理-小明）发出的中秋祝福，该消息之后客户 Liana Jin 没有任何新消息。",
        "再没有发过任何消息。最近几条消息都是你这边发出的——昨晚的中秋祝福，以及刚刚的[玫瑰]。",
        "聊天记录显示，最近两条消息都是我发出的：\n1. 2026-09-24 中秋祝福\n2. 刚才的[玫瑰]\n客户在此之后没有新消息。",
        "目前这个对话中，只有您这边发出的两条消息（昨天的中秋祝福和刚才的[玫瑰]），客户\"休闲小猫\"并没有发送过任何消息过来。所以暂时没有需要回复的客户消息。",
    ]
    for sample in samples:
        assert ai_panel.classify_response(sample).status == "no_inbound", sample[:50]

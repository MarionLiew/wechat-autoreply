import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wecom import selectors
from wecom.mac_watcher import WeChatWatcher


def node(role, *children, value=""):
    return SimpleNamespace(AXRole=role, AXChildren=list(children), AXValue=value)


def conversation_table():
    return node("AXTable", node("AXRow", node("AXCell",
                node("AXImage"),
                node("AXStaticText", value="客户"),
                node("AXStaticText", value="消息预览"))))


class ConversationSelectorTests(unittest.TestCase):
    def setUp(self):
        self.dump = patch.object(selectors, "_maybe_dump").start()
        self.addCleanup(patch.stopall)
        self.watcher = WeChatWatcher.__new__(WeChatWatcher)

    def test_absent_empty_and_blank_tables_fail_closed(self):
        for window in (
            node("AXWindow"),
            node("AXWindow", node("AXTable")),
            node("AXWindow", node("AXTable", node("AXRow", node("AXCell",
                 node("AXStaticText", value="  "))))),
        ):
            with self.subTest(window=window):
                self.assertIsNone(selectors.find_conversation_table(window))
                with self.assertRaisesRegex(RuntimeError, "AXTable"):
                    self.watcher._find_conversation_list(window)

    def test_single_conversation_with_bot_marker_is_supported(self):
        table = conversation_table()
        table.AXChildren[0].AXChildren[0].AXChildren.append(
            node("AXTextArea", value="BOT"))
        window = node("AXWindow", node("AXSplitGroup", node("AXScrollArea", table)))
        self.assertIs(table, selectors.find_conversation_table(window))
        self.assertIs(table, self.watcher._find_conversation_list(window))

    def test_message_body_tables_are_not_conversations(self):
        body = node("AXTable", node("AXRow", node("AXCell",
                    node("AXTextArea", node("AXStaticText", value="正文")))))
        web_body = node("AXWebArea", conversation_table())
        for message_panel in (body, web_body):
            with self.subTest(role=message_panel.AXRole):
                self.assertIsNone(selectors.find_conversation_table(
                    node("AXWindow", message_panel)))
                conversations = conversation_table()
                window = node("AXWindow", message_panel, conversations)
                self.assertIs(conversations, selectors.find_conversation_table(window))
                self.assertIs(conversations, self.watcher._find_conversation_list(window))

    def test_ambiguous_text_tables_fail_closed(self):
        window = node("AXWindow", conversation_table(), conversation_table())
        self.assertIsNone(selectors.find_conversation_table(window))
        with self.assertRaisesRegex(RuntimeError, "AXTable"):
            self.watcher._find_conversation_list(window)
        self.assertTrue(self.dump.called)

    def test_native_body_static_rows_do_not_compete_with_single_conversation(self):
        # Saved probe: body samples [['10:20'], [], [], ['内容由AI生成']].
        # The truncated AX dump confirms conversation cells have direct avatars
        # and static text; it does not include the body's full subtree.
        body = node("AXTable", *[
            node("AXRow", node("AXCell", *[
                node("AXStaticText", value=text) for text in texts
            ]))
            for texts in (["10:20"], [], [], ["内容由AI生成"])
        ])
        conversation = conversation_table()
        for tables in ((body,), (body, conversation), (conversation, body)):
            with self.subTest(order=[t is conversation for t in tables]):
                window = node("AXWindow", *tables)
                if len(tables) == 1:
                    self.assertIsNone(selectors.find_conversation_table(window))
                    with self.assertRaisesRegex(RuntimeError, "AXTable"):
                        self.watcher._find_conversation_list(window)
                else:
                    self.assertIs(conversation, selectors.find_conversation_table(window))
                    self.assertIs(conversation, self.watcher._find_conversation_list(window))

    def test_cancelled_call_two_icon_cell_is_not_a_conversation(self):
        # Exact direct-child shape captured in wecom_candidates.json.
        body = node("AXTable", node("AXRow", node("AXCell",
                    node("AXImage"), node("AXImage"),
                    node("AXStaticText", value="对方已取消"))))
        conversation = conversation_table()
        for tables in ((body,), (body, conversation), (conversation, body)):
            with self.subTest(order=[t is conversation for t in tables]):
                window = node("AXWindow", *tables)
                if len(tables) == 1:
                    self.assertIsNone(selectors.find_conversation_table(window))
                    with self.assertRaisesRegex(RuntimeError, "AXTable"):
                        self.watcher._find_conversation_list(window)
                else:
                    self.assertIs(conversation, selectors.find_conversation_table(window))
                    self.assertIs(conversation, self.watcher._find_conversation_list(window))

    def test_message_text_area_vetoes_otherwise_qualifying_table(self):
        # Veto applies to the entire table, even after a qualifying first row.
        for value in ("你好", "", "BOT 客户消息", "外部消息"):
            for nested in (False, True):
                with self.subTest(value=value, nested=nested):
                    body = conversation_table()
                    text = node("AXTextArea", value=value)
                    if nested:
                        text = node("AXGroup", text)
                    body.AXChildren.append(node("AXRow", node("AXCell", text)))
                    conversation = conversation_table()
                    self.assertIsNone(selectors.find_conversation_table(
                        node("AXWindow", body)))
                    window = node("AXWindow", body, conversation)
                    self.assertIs(conversation, self.watcher._find_conversation_list(window))

    def test_small_conversation_text_area_markers_are_allowed(self):
        for marker in ("BOT", "外部", "BOT\u200b", "外部\u200b"):
            with self.subTest(marker=marker):
                conversation = conversation_table()
                conversation.AXChildren[0].AXChildren[0].AXChildren.append(
                    node("AXTextArea", value=marker))
                self.assertIs(conversation, self.watcher._find_conversation_list(
                    node("AXWindow", conversation)))

    def test_navigation_table_before_conversations(self):
        navigation = node("AXTable", *[
            node("AXRow", node("AXCell", node("AXButton"), node("AXImage")))
            for _ in range(12)
        ])
        conversations = conversation_table()
        window = node("AXWindow", navigation, conversations)
        self.assertIs(conversations, selectors.find_conversation_table(window))
        self.assertIs(conversations, self.watcher._find_conversation_list(window))
        self.dump.assert_not_called()


if __name__ == "__main__":
    unittest.main()

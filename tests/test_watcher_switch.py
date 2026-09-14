import unittest
from unittest.mock import patch

from wecom.mac_watcher import WeChatWatcher


class ConversationSwitchTests(unittest.TestCase):
    def _watcher(self, state):
        watcher = WeChatWatcher.__new__(WeChatWatcher)
        watcher._get_main_window = lambda: object()
        watcher._active_chat_sender = lambda _window: state["sender"]
        watcher._find_conv_row_by_sender = lambda _sender: "fresh-row"
        watcher._try_dismiss_covering_panel = lambda _window: False
        return watcher

    @patch("wecom.mac_watcher._get_wecom_pid", return_value=123)
    @patch("wecom.mac_watcher._osascript_click_conv_row")
    @patch("wecom.mac_watcher._force_click_conv_row")
    @patch("wecom.mac_watcher._press_conv_row")
    def test_background_ax_switch_is_verified_without_foreground(
        self, press, force, foreground, _pid
    ):
        state = {"sender": "其他人"}
        watcher = self._watcher(state)
        press.side_effect = lambda _row: state.update(sender="目标客户") or True

        with patch.object(__import__("wecom.mac_watcher", fromlist=["settings"]).settings,
                          "background_mode", True), \
             patch.object(__import__("wecom.mac_watcher", fromlist=["settings"]).settings,
                          "allow_foreground_fallback", True):
            result = watcher._switch_to_conv_and_verify(
                "stale-row", "目标客户(男)-1234", per_attempt_timeout=0.01
            )

        self.assertTrue(result)
        press.assert_called_once_with("fresh-row")
        force.assert_not_called()
        foreground.assert_not_called()

    @patch("wecom.mac_watcher._get_wecom_pid", return_value=123)
    @patch("wecom.mac_watcher._osascript_click_conv_row")
    @patch("wecom.mac_watcher._force_click_conv_row", return_value=True)
    @patch("wecom.mac_watcher._press_conv_row", return_value=True)
    def test_strict_background_mode_never_uses_foreground(
        self, _press, _force, foreground, _pid
    ):
        state = {"sender": "其他人"}
        watcher = self._watcher(state)

        with patch.object(__import__("wecom.mac_watcher", fromlist=["settings"]).settings,
                          "background_mode", True), \
             patch.object(__import__("wecom.mac_watcher", fromlist=["settings"]).settings,
                          "allow_foreground_fallback", False):
            result = watcher._switch_to_conv_and_verify(
                "row", "目标客户(男)-1234", per_attempt_timeout=0.001
            )

        self.assertFalse(result)
        foreground.assert_not_called()


if __name__ == "__main__":
    unittest.main()

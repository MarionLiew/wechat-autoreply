import unittest
from unittest.mock import MagicMock, patch

from wecom.mac_watcher import WeChatWatcher


class TestWindowSelfHeal(unittest.TestCase):
    def _watcher(self, app):
        watcher = WeChatWatcher.__new__(WeChatWatcher)
        watcher._app = app
        watcher._app_fail_streak = 0
        watcher._window_fail_streak = 0
        return watcher

    @patch("wecom.mac_watcher.time.sleep")
    @patch("wecom.mac_watcher.subprocess.run")
    @patch("wecom.mac_watcher.atomacos.getAppRefByBundleId")
    def test_repeated_axwindows_failure_reopens_and_validates_window(
        self, get_app, run, _sleep
    ):
        broken_apps = []
        for _ in range(3):
            app = MagicMock()
            type(app).AXWindows = property(
                lambda _self: (_ for _ in ()).throw(RuntimeError("stale AX tree"))
            )
            broken_apps.append(app)

        window = MagicMock()
        window.AXTitle = "企业微信"
        recovered_app = MagicMock()
        recovered_app.AXWindows = [window]
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        get_app.side_effect = [broken_apps[1], broken_apps[2], recovered_app]
        watcher = self._watcher(broken_apps[0])

        with self.assertRaisesRegex(RuntimeError, "无法枚举"):
            watcher._get_main_window()
        with self.assertRaisesRegex(RuntimeError, "无法枚举"):
            watcher._get_main_window()

        self.assertIs(watcher._get_main_window(), window)
        run.assert_called_once_with(
            ["open", "-b", "com.tencent.WeWorkMac"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(watcher._window_fail_streak, 0)

    @patch("wecom.mac_watcher.time.sleep")
    @patch("wecom.mac_watcher.subprocess.run")
    @patch("wecom.mac_watcher.atomacos.getAppRefByBundleId")
    def test_failed_recovery_keeps_streak_and_reports_failure(
        self, get_app, run, _sleep
    ):
        broken_apps = []
        for _ in range(4):
            app = MagicMock()
            type(app).AXWindows = property(
                lambda _self: (_ for _ in ()).throw(RuntimeError("stale AX tree"))
            )
            broken_apps.append(app)
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        get_app.side_effect = broken_apps[1:]
        watcher = self._watcher(broken_apps[0])

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "无法枚举"):
                watcher._get_main_window()

        with self.assertRaisesRegex(RuntimeError, "自愈失败"):
            watcher._get_main_window()

        run.assert_called_once()
        self.assertEqual(watcher._window_fail_streak, 3)


if __name__ == "__main__":
    unittest.main()

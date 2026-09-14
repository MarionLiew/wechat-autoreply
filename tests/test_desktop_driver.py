import unittest
from unittest.mock import patch

from config import Settings
from wecom.desktop_driver import (
    ActionEffect,
    BackgroundDesktopDriver,
    foreground_fallback_enabled,
)


class BackgroundDesktopDriverTests(unittest.TestCase):
    def test_foreground_policy_preserves_legacy_mode(self):
        self.assertTrue(foreground_fallback_enabled(False, False))
        self.assertTrue(foreground_fallback_enabled(True, True))
        self.assertFalse(foreground_fallback_enabled(True, False))

    def test_settings_default_to_background_first_with_foreground_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertTrue(settings.background_mode)
        self.assertTrue(settings.allow_foreground_fallback)

    def test_background_ax_success_never_calls_other_routes(self):
        calls = []
        driver = BackgroundDesktopDriver(
            ax_action=lambda: calls.append("ax") or True,
            pid_action=lambda: calls.append("pid") or True,
            foreground_action=lambda: calls.append("foreground") or True,
            verify=lambda: True,
            allow_foreground_fallback=True,
        )

        result = driver.perform()

        self.assertEqual(ActionEffect.CONFIRMED, result.effect)
        self.assertEqual("ax", result.method)
        self.assertFalse(result.foreground_used)
        self.assertEqual(["ax"], calls)

    def test_failed_ax_uses_pid_route_before_foreground(self):
        calls = []
        verified = iter([False, True])
        driver = BackgroundDesktopDriver(
            ax_action=lambda: calls.append("ax") or True,
            pid_action=lambda: calls.append("pid") or True,
            foreground_action=lambda: calls.append("foreground") or True,
            verify=lambda: next(verified),
            allow_foreground_fallback=True,
        )

        result = driver.perform()

        self.assertEqual(ActionEffect.CONFIRMED, result.effect)
        self.assertEqual("pid", result.method)
        self.assertFalse(result.foreground_used)
        self.assertEqual(["ax", "pid"], calls)

    def test_strict_background_mode_never_calls_foreground(self):
        calls = []
        driver = BackgroundDesktopDriver(
            ax_action=lambda: calls.append("ax") or True,
            pid_action=lambda: calls.append("pid") or True,
            foreground_action=lambda: calls.append("foreground") or True,
            verify=lambda: False,
            allow_foreground_fallback=False,
        )

        result = driver.perform()

        self.assertEqual(ActionEffect.SUSPECTED_NOOP, result.effect)
        self.assertEqual("pid", result.method)
        self.assertFalse(result.foreground_used)
        self.assertEqual(["ax", "pid"], calls)

    def test_foreground_is_last_resort_and_must_verify(self):
        calls = []
        verified = iter([False, False, True])
        driver = BackgroundDesktopDriver(
            ax_action=lambda: calls.append("ax") or True,
            pid_action=lambda: calls.append("pid") or True,
            foreground_action=lambda: calls.append("foreground") or True,
            verify=lambda: next(verified),
            allow_foreground_fallback=True,
        )

        result = driver.perform()

        self.assertEqual(ActionEffect.CONFIRMED, result.effect)
        self.assertEqual("foreground", result.method)
        self.assertTrue(result.foreground_used)
        self.assertEqual(["ax", "pid", "foreground"], calls)


if __name__ == "__main__":
    unittest.main()

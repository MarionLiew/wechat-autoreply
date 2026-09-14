"""Background-first desktop action routing for WeCom on macOS.

This module is deliberately independent of atomacos and Quartz. Callers inject
platform-specific actions and a state verifier, which keeps the escalation
policy deterministic and unit-testable without a live WeCom process.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ActionEffect(str, Enum):
    CONFIRMED = "confirmed"
    SUSPECTED_NOOP = "suspected_noop"


def foreground_fallback_enabled(
    background_mode: bool, allow_foreground_fallback: bool
) -> bool:
    """Legacy mode is foreground-capable; strict background mode is not."""
    return not background_mode or allow_foreground_fallback


@dataclass(frozen=True)
class ActionResult:
    effect: ActionEffect
    method: str
    foreground_used: bool = False


class BackgroundDesktopDriver:
    """Try AX, then PID-targeted input, then optional foreground fallback."""

    def __init__(
        self,
        *,
        ax_action: Callable[[], bool],
        pid_action: Callable[[], bool],
        foreground_action: Callable[[], bool],
        verify: Callable[[], bool],
        allow_foreground_fallback: bool,
    ) -> None:
        self._ax_action = ax_action
        self._pid_action = pid_action
        self._foreground_action = foreground_action
        self._verify = verify
        self._allow_foreground_fallback = allow_foreground_fallback

    def perform(self) -> ActionResult:
        for method, action in (("ax", self._ax_action), ("pid", self._pid_action)):
            if self._attempt(action) and self._verified():
                return ActionResult(ActionEffect.CONFIRMED, method)

        if self._allow_foreground_fallback:
            if self._attempt(self._foreground_action) and self._verified():
                return ActionResult(
                    ActionEffect.CONFIRMED,
                    "foreground",
                    foreground_used=True,
                )
            return ActionResult(
                ActionEffect.SUSPECTED_NOOP,
                "foreground",
                foreground_used=True,
            )

        return ActionResult(ActionEffect.SUSPECTED_NOOP, "pid")

    @staticmethod
    def _attempt(action: Callable[[], bool]) -> bool:
        try:
            return bool(action())
        except Exception:
            return False

    def _verified(self) -> bool:
        try:
            return bool(self._verify())
        except Exception:
            return False

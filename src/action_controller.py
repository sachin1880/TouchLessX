"""Action Dispatcher: converts validated gestures into mouse/keyboard commands.

The actual mouse/keyboard calls are isolated behind the small `InputExecutor`
interface so the mapping/gating logic here can be unit tested with a fake executor,
with no real camera, display, or OS input side effects (see tests/test_mapping.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .gesture_engine import GestureEvent
from .safety import SafetyGuard


class InputExecutor(Protocol):
    def move_mouse(self, x: int, y: int) -> None: ...
    def click(self, button: str = "left") -> None: ...
    def press_key(self, key: str) -> None: ...


class PyAutoGuiExecutor:
    """Default executor - drives the real mouse/keyboard via pyautogui."""

    def __init__(self) -> None:
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
        self._pyautogui = pyautogui
        self.screen_size = pyautogui.size()

    def move_mouse(self, x: int, y: int) -> None:
        self._pyautogui.moveTo(x, y, _pause=False)

    def click(self, button: str = "left") -> None:
        self._pyautogui.click(button=button)

    def press_key(self, key: str) -> None:
        self._pyautogui.press(key)


class NullExecutor:
    """Fallback when pyautogui can't initialize (no display, missing OS input/
    accessibility permission, headless environment, ...). The app keeps running and
    showing detection - it just doesn't move the mouse or send key presses."""

    def __init__(self) -> None:
        self.screen_size = (1920, 1080)

    def move_mouse(self, x: int, y: int) -> None:
        pass

    def click(self, button: str = "left") -> None:
        pass

    def press_key(self, key: str) -> None:
        pass


@dataclass
class DispatchResult:
    label: str
    category: str


# gesture name -> (config category that must be enabled, human label, key to press)
_KEY_ACTIONS = {
    "fist": ("media_control", "Play / Pause", "playpause"),
    "swipe_left": ("presentation", "Previous Slide", "left"),
    "swipe_right": ("presentation", "Next Slide", "right"),
}


class ActionController:
    def __init__(
        self,
        executor: InputExecutor,
        safety: SafetyGuard,
        enabled: dict[str, bool],
        cursor_smoothing: float = 0.5,
        screen_size: tuple[int, int] | None = None,
    ) -> None:
        self.executor = executor
        self.safety = safety
        self.enabled = enabled
        self.cursor_smoothing = cursor_smoothing
        self.screen_w, self.screen_h = screen_size or getattr(executor, "screen_size", (1920, 1080))
        self._smoothed_xy: tuple[float, float] | None = None

    def is_enabled(self, category: str) -> bool:
        return bool(self.enabled.get(category, False))

    def dispatch(self, event: GestureEvent) -> DispatchResult | None:
        """FR-04/FR-05/FR-06: map a validated gesture to an action, unless paused or
        the gesture's confidence is too low to trust."""
        if not event.is_trigger:
            return None
        if self.safety.paused:
            return None
        if not self.safety.passes_confidence(event.confidence):
            return None

        if event.name == "open_palm":
            return self._move_cursor(event)
        if event.name == "point":
            return self._adjust_volume(event)
        if event.name == "pinch":
            return self._click("left")
        if event.name == "two_fingers":
            return self._click("right")
        if event.name in _KEY_ACTIONS:
            category, label, key = _KEY_ACTIONS[event.name]
            if not self.is_enabled(category):
                return None
            self.executor.press_key(key)
            return DispatchResult(label, category)
        return None

    def _move_cursor(self, event: GestureEvent) -> DispatchResult | None:
        if not self.is_enabled("mouse_control") or event.index_tip is None:
            return None
        target_x = event.index_tip[0] * self.screen_w
        target_y = event.index_tip[1] * self.screen_h
        if self._smoothed_xy is None:
            self._smoothed_xy = (target_x, target_y)
        else:
            alpha = max(0.01, min(1.0, 1.0 - self.cursor_smoothing))
            prev_x, prev_y = self._smoothed_xy
            self._smoothed_xy = (prev_x + (target_x - prev_x) * alpha, prev_y + (target_y - prev_y) * alpha)
        x, y = self._smoothed_xy
        self.executor.move_mouse(int(x), int(y))
        return DispatchResult("Mouse Move", "mouse_control")

    def _click(self, button: str) -> DispatchResult | None:
        if not self.is_enabled("mouse_control"):
            return None
        self.executor.click(button=button)
        return DispatchResult("Left Click" if button == "left" else "Right Click", "mouse_control")

    def _adjust_volume(self, event: GestureEvent) -> DispatchResult | None:
        if not self.is_enabled("volume_control") or event.motion_delta is None:
            return None
        _, dy = event.motion_delta
        threshold = 0.012
        if dy < -threshold:
            self.executor.press_key("volumeup")
            return DispatchResult("Volume Up", "volume_control")
        if dy > threshold:
            self.executor.press_key("volumedown")
            return DispatchResult("Volume Down", "volume_control")
        return None

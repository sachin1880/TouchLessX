"""Tests for ActionController's gesture -> action mapping and gating logic.

Uses a FakeExecutor instead of PyAutoGuiExecutor so these run with no real display,
mouse, or keyboard side effects.
"""
from __future__ import annotations

from src.action_controller import ActionController
from src.gesture_engine import GestureEvent
from src.safety import SafetyGuard


class FakeExecutor:
    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[str] = []
        self.keys: list[str] = []
        self.screen_size = (1000, 1000)

    def move_mouse(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def click(self, button: str = "left") -> None:
        self.clicks.append(button)

    def press_key(self, key: str) -> None:
        self.keys.append(key)


def _controller(enabled: dict | None = None) -> tuple[ActionController, FakeExecutor, SafetyGuard]:
    executor = FakeExecutor()
    safety = SafetyGuard(confidence_threshold=0.6)
    enabled = enabled if enabled is not None else {
        "mouse_control": True, "media_control": True, "presentation": True, "volume_control": True,
    }
    controller = ActionController(executor, safety, enabled, cursor_smoothing=0.0)
    return controller, executor, safety


def test_pinch_triggers_left_click():
    controller, executor, _ = _controller()
    event = GestureEvent("pinch", confidence=90, index_tip=(0.5, 0.5), motion_delta=None, is_trigger=True)
    result = controller.dispatch(event)
    assert result is not None and result.label == "Left Click"
    assert executor.clicks == ["left"]


def test_two_fingers_triggers_right_click():
    controller, executor, _ = _controller()
    event = GestureEvent("two_fingers", confidence=90, index_tip=None, motion_delta=None, is_trigger=True)
    controller.dispatch(event)
    assert executor.clicks == ["right"]


def test_open_palm_moves_mouse_using_index_tip_position():
    controller, executor, _ = _controller()
    event = GestureEvent("open_palm", confidence=90, index_tip=(0.25, 0.75), motion_delta=None, is_trigger=True)
    controller.dispatch(event)
    assert executor.moves == [(250, 750)]  # 1000x1000 fake screen, no smoothing


def test_disabled_category_blocks_the_action():
    controller, executor, _ = _controller(enabled={"mouse_control": False})
    event = GestureEvent("pinch", confidence=90, index_tip=None, motion_delta=None, is_trigger=True)
    result = controller.dispatch(event)
    assert result is None
    assert executor.clicks == []


def test_paused_blocks_every_action():
    controller, executor, safety = _controller()
    safety.toggle_pause()
    event = GestureEvent("pinch", confidence=90, index_tip=None, motion_delta=None, is_trigger=True)
    assert controller.dispatch(event) is None
    assert executor.clicks == []


def test_low_confidence_is_ignored():
    controller, executor, _ = _controller()
    event = GestureEvent("pinch", confidence=10, index_tip=None, motion_delta=None, is_trigger=True)
    assert controller.dispatch(event) is None
    assert executor.clicks == []


def test_non_trigger_event_is_ignored_even_if_otherwise_valid():
    controller, executor, _ = _controller()
    event = GestureEvent("pinch", confidence=90, index_tip=None, motion_delta=None, is_trigger=False)
    assert controller.dispatch(event) is None
    assert executor.clicks == []


def test_swipe_right_sends_next_slide_key_when_presentation_enabled():
    controller, executor, _ = _controller()
    event = GestureEvent("swipe_right", confidence=90, index_tip=None, motion_delta=None, is_trigger=True)
    result = controller.dispatch(event)
    assert result is not None and result.label == "Next Slide"
    assert executor.keys == ["right"]


def test_fist_play_pause_blocked_when_media_control_disabled():
    controller, executor, _ = _controller(enabled={"media_control": False})
    event = GestureEvent("fist", confidence=90, index_tip=None, motion_delta=None, is_trigger=True)
    assert controller.dispatch(event) is None
    assert executor.keys == []

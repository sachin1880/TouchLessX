"""Tests for the JARVIS features: virtual keyboard, one-hand zoom, native window
management, desktop switching, the JARVIS profile and theme, and system statistics.
No camera, display, network or real input needed."""
from __future__ import annotations

import platform

from src.action_controller import DispatchResult, action_spec, edit_keys
from src.gesture_engine import GestureEngine, classify_static_gesture
from src.profiles import LATER_BUILTINS, builtin_profiles, validate_profile
from src.system_stats import parse_battery, parse_cpu, parse_memory
from src.virtual_keyboard import REPEAT_GUARD_SECONDS, SHIFT, KeyboardModel, key_label
from tests.test_features import _controller, _event
from tests.test_gestures import _FakeHandResult, _hand


# ------------------------------------------------------------------ keyboard
def _keyboard():
    model = KeyboardModel()
    model.visible = True
    model.set_layout((100, 500, 600, 300), [("a", 110, 600, 50, 50), ("⌫", 170, 600, 70, 50),
                                            (SHIFT, 250, 600, 70, 50), ("space", 110, 700, 300, 50)])
    return model


def test_the_key_under_the_cursor_is_found():
    model = _keyboard()
    assert model.key_at(130, 620) == "a" and model.key_at(300, 720) == "space"
    assert model.key_at(105, 520) is None  # on the keyboard, between keys
    assert model.contains(105, 520) and not model.contains(50, 50)


def test_a_hidden_keyboard_covers_nothing():
    model = _keyboard()
    model.visible = False
    assert not model.contains(130, 620)


def test_keys_type_text_or_press_special_keys():
    model = _keyboard()
    assert model.press("a", 1.0) == ("text", "a")
    assert model.press("space", 2.0) == ("key", "space")
    assert model.press("⌫", 3.0) == ("key", "backspace")
    assert model.typed == "a"


def test_shift_capitalises_one_key():
    model = _keyboard()
    assert model.press(SHIFT, 1.0) == ("shift", "")
    assert model.press("a", 2.0) == ("text", "A")
    assert model.press("a", 3.0) == ("text", "a")
    assert key_label("1", shift=True) == "!"


def test_a_second_press_too_soon_is_ignored():
    model = _keyboard()
    assert model.press("a", 1.0) is not None
    assert model.press("a", 1.0 + REPEAT_GUARD_SECONDS / 2) is None
    assert model.press("a", 1.0 + REPEAT_GUARD_SECONDS + 0.01) is not None


def test_a_click_can_be_turned_into_a_key_instead():
    controller, executor, _ = _controller()
    controller.callbacks["pointer_click"] = lambda action: DispatchResult("Key a", "")
    assert controller.run_action("left_click").label == "Key a"
    assert not any(c[0] == "click" for c in executor.calls)  # no real click reached the screen
    controller.callbacks["pointer_click"] = lambda action: None
    controller.run_action("left_click")
    assert ("click", "left") in executor.calls


def test_a_held_pinch_over_the_keyboard_does_not_start_a_drag():
    controller, executor, _ = _controller(drag_enabled=True, drag_hold_seconds=0.3)
    controller.callbacks["pointer_blocked"] = lambda: True
    controller.dispatch(_event("pinch"))
    controller.dispatch(_event("pinch", trigger=False, hold=0.5))
    assert ("down",) not in executor.calls


# ------------------------------------------------------------------ one-hand zoom
def _l_pose():
    return _hand(index=True, thumb=True)  # index up, thumb out to the side


def test_the_l_pose_is_thumb_index_only_while_it_is_mapped():
    engine = GestureEngine(stable_frames=1)
    engine.muted = {"thumb_index"}
    assert engine.update(_FakeHandResult(_l_pose()), 1.0).name == "point"
    engine = GestureEngine(stable_frames=1)
    assert engine.update(_FakeHandResult(_l_pose()), 1.0).name == "thumb_index"
    assert classify_static_gesture(_l_pose()) == "point"  # the plain rules are unchanged


def test_spreading_the_l_zooms_in_and_closing_it_never_clicks():
    engine = GestureEngine(stable_frames=1)
    engine.continuous_gestures = {"thumb_index"}
    controller, executor, _ = _controller()
    controller.mappings = {"thumb_index": "zoom"}
    controller.continuous_gestures = {"thumb_index"}
    executor.hotkey = lambda keys: executor.calls.append(("hotkey", tuple(keys)))
    hand = _l_pose()
    t = 1.0
    for step in range(12):  # thumb moving further out: the gap widens
        hand = hand.copy()
        hand[4] = [0.30 - 0.02 * step, 0.75, 0.0]
        controller.dispatch(engine.update(_FakeHandResult(hand), t))
        t += 0.05
    zoom_in = edit_keys("zoom_in")
    assert ("hotkey", tuple(zoom_in)) in executor.calls
    closed = _hand(pinch=True, middle=False)
    closed[4], closed[8] = [0.50, 0.50, 0.0], [0.50, 0.505, 0.0]
    event = engine.update(_FakeHandResult(closed), t)
    assert event.name == "thumb_index"  # still zooming, not a pinch
    assert ("click", "left") not in executor.calls


# ------------------------------------------------------------------ windows and desktops
class FakeWindows:
    available = True

    def __init__(self):
        self.frames, self.arranged = [], []

    def grab(self):
        return "window"

    def frame(self, window):
        return (100.0, 100.0, 800.0, 600.0)

    def set_frame(self, window, x=None, y=None, width=None, height=None):
        self.frames.append((x, y, width, height))
        return True

    def arrange(self, layout):
        self.arranged.append(layout)
        return True


def _window_controller():
    controller, executor, _ = _controller(engage_seconds=0.2)
    controller.window_manager = FakeWindows()
    controller.mappings = {"three_fingers": "move_window", "fist": "resize_window"}
    controller.continuous_gestures = {"three_fingers", "fist"}
    return controller, executor


def test_a_held_pose_picks_up_the_front_window_and_moves_it_with_the_hand():
    controller, _ = _window_controller()
    windows = controller.window_manager
    assert controller.dispatch(_event("three_fingers", hold=0.1, delta=(0.1, 0.0))) is None  # not yet engaged
    assert controller.dispatch(_event("three_fingers", hold=0.3, delta=(0.0, 0.0))).label == "Move Window"
    controller.dispatch(_event("three_fingers", hold=0.35, delta=(0.1, -0.05)))
    x, y, _, _ = windows.frames[-1]
    assert x > 100 and y < 100  # right and up, like the hand


def test_resizing_follows_the_hand_and_keeps_a_minimum_size():
    controller, _ = _window_controller()
    windows = controller.window_manager
    controller.dispatch(_event("fist", hold=0.3, delta=(0.0, 0.0)))
    controller.dispatch(_event("fist", hold=0.35, delta=(-2.0, -2.0)))
    _, _, width, height = windows.frames[-1]
    assert width >= 320 and height >= 200


def test_practice_mode_never_touches_real_windows():
    controller, _ = _window_controller()
    controller.dry_run = True
    controller.dispatch(_event("three_fingers", hold=0.3, delta=(0.0, 0.0)))
    controller.dispatch(_event("three_fingers", hold=0.35, delta=(0.1, 0.1)))
    controller.run_action("window_left")
    assert controller.window_manager.frames == [] and controller.window_manager.arranged == []


def test_snapping_and_desktop_switching():
    controller, executor, _ = _controller()
    controller.window_manager = FakeWindows()
    controller.run_action("window_left")
    controller.run_action("window_maximize")
    assert controller.window_manager.arranged == ["left", "maximize"]
    executor.hotkey = lambda keys: executor.calls.append(("hotkey", tuple(keys)))
    controller.run_action("next_workspace")
    controller.run_action("prev_workspace")
    expected = ("ctrl", "right") if platform.system() == "Darwin" else ("ctrl", "win", "right")
    assert ("hotkey", expected) in executor.calls


def test_scroll_steps_and_the_keyboard_action():
    controller, executor, _ = _controller()
    controller.run_action("scroll_up")
    controller.run_action("scroll_down")
    scrolls = [c[1] for c in executor.calls if c[0] == "scroll"]
    assert scrolls[0] > 0 > scrolls[1]
    toggled = []
    controller.callbacks["toggle_keyboard"] = lambda: toggled.append(True)
    assert controller.run_action("toggle_keyboard").label == "Show / Hide Keyboard"
    assert toggled


# ------------------------------------------------------------------ profile and theme
def test_the_jarvis_profile_follows_the_specification():
    profile = validate_profile(builtin_profiles()["JARVIS"])
    m = profile["mappings"]
    assert (m["point"], m["pinch"], m["open_palm"]) == ("move_cursor", "left_click", "safety_hold")
    assert (m["swipe_left"], m["swipe_right"]) == ("prev_workspace", "next_workspace")
    assert (m["swipe_up"], m["swipe_down"]) == ("scroll_up", "scroll_down")
    assert (m["thumb_index"], m["two_fingers"], m["fist"]) == ("zoom", "right_click", "emergency_stop")
    assert profile["drag_enabled"] and "JARVIS" in LATER_BUILTINS


def test_new_actions_are_known():
    for action in ("move_window", "resize_window", "window_left", "window_right", "window_maximize",
                   "window_center", "next_workspace", "prev_workspace", "mission_control", "scroll_up",
                   "scroll_down", "toggle_keyboard"):
        assert action_spec(action) is not None


def test_the_jarvis_theme_switches_the_dark_surfaces_and_back():
    from src import ui_widgets

    standard = ui_widgets.BG[1]
    ui_widgets.set_jarvis(True)
    assert ui_widgets.BG[1] != standard and ui_widgets.BG[0] == "#eef2f8"  # the light theme is untouched
    ui_widgets.set_jarvis(False)
    assert ui_widgets.BG[1] == standard


# ------------------------------------------------------------------ system statistics
def test_cpu_memory_and_battery_are_read_from_the_system_tools():
    assert parse_cpu(" %CPU\n 50.0\n 30,0\n 20.0\n", 4) == 25.0
    vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1000.\n"
          "Pages active: 100000.\nPages wired down: 50000.\nPages occupied by compressor: 50000.\n")
    assert abs(parse_memory(vm, 16 * 1024 ** 3) - 19.07) < 0.1
    assert parse_battery("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t78%; charging; 0:40") == (78.0, True)
    assert parse_battery("Now drawing from 'AC Power'\n") is None
    assert parse_memory("garbage", 100) is None

"""Tests for profiles, keyboard shortcuts, confirmation, practice mode, the extra
gestures and the local history. No camera, display, network or real input needed."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from src.action_controller import (
    ActionController, ShortcutError, actions_for_gesture, parse_shortcut, shortcut_text,
)
from src.config import DEFAULT_CONFIG
from src.gesture_engine import GestureEngine, classify_static_gesture
from src.history import History
from src.profiles import PROFILE_KEYS, ProfileError, ProfileStore, builtin_profiles, validate_profile
from src.safety import SafetyGuard
from tests.test_features import ALL_ON, RecordingExecutor, _Clock, _event
from tests.test_gestures import _FakeHandResult, _hand


def _controller(**kwargs):
    executor = RecordingExecutor()
    executor.hotkey = lambda keys: executor.calls.append(("hotkey", tuple(keys)))
    controller = ActionController(executor, SafetyGuard(), dict(ALL_ON), cursor_smoothing=0.0, **kwargs)
    return controller, executor


# ------------------------------------------------------------------ gestures
def test_thumbs_up_needs_the_thumb_to_point_up():
    up = _hand(thumb=True)
    up[4] = [0.42, 0.50, 0.0]
    assert classify_static_gesture(up) == "thumbs_up"
    sideways = _hand(thumb=True)  # thumb out to the side, level with the palm: just a fist
    assert classify_static_gesture(sideways) == "fist"
    assert classify_static_gesture(_hand()) == "fist"


def test_vertical_swipes_are_told_apart_from_horizontal_ones():
    def sweep(dx, dy):
        engine = GestureEngine(swipe_min_distance=0.2, swipe_max_duration=0.4)
        fired = []
        for i in range(10):
            hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
            hand[:, 0] += dx * i
            hand[:, 1] += dy * i
            event = engine.update(_FakeHandResult(hand), now=i * 0.035)
            if event.is_trigger and event.name.startswith("swipe"):
                fired.append(event.name)
        return fired

    assert sweep(0.0, -0.06) == ["swipe_up"]
    assert sweep(0.0, 0.06) == ["swipe_down"]
    assert sweep(-0.06, 0.0) == ["swipe_left"]
    assert sweep(0.05, 0.05) == []  # diagonal: ambiguous, so nothing


def test_poses_released_too_early_are_counted():
    engine = GestureEngine(pose_hold_seconds=0.3)
    palm = _FakeHandResult(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True))
    fist = _FakeHandResult(_hand())
    for start in (0.0, 1.0):
        engine.update(fist, now=start)
        engine.update(palm, now=start + 0.1)
    engine.update(fist, now=2.0)
    engine.update(fist, now=2.4)  # held: fires, not counted
    engine.update(palm, now=2.5)
    assert engine.brief_poses == {"fist": 2}


# ----------------------------------------------------------------- shortcuts
def test_shortcuts_are_parsed_and_limited_to_approved_keys():
    assert parse_shortcut("Cmd + Shift + T") == ["command", "shift", "t"]
    assert parse_shortcut("shift+ctrl+Right") == ["ctrl", "shift", "right"]  # canonical modifier order
    assert parse_shortcut(["alt", "f4"]) == ["alt", "f4"]
    assert shortcut_text(["command", "shift", "t"]) == "Command + Shift + T"
    for bad in ("", "Cmd", "Cmd+Shift", "a+b", "Cmd+Cmd+a", "Cmd+power", "ctrl+rm -rf", "Cmd+volumeup"):
        with pytest.raises(ShortcutError):
            parse_shortcut(bad)


def test_gesture_mapped_to_a_shortcut_presses_exactly_those_keys():
    controller, executor = _controller(mappings={"fist": "shortcut:Switch app", "two_fingers": "shortcut:Gone"},
                                       shortcuts={"Switch app": ["command", "tab"]})
    assert controller.dispatch(_event("fist")).label == "Switch app"
    assert controller.dispatch(_event("two_fingers")) is None  # mapping to a shortcut that no longer exists
    assert executor.calls == [("hotkey", ("command", "tab"))]
    assert "shortcut:Switch app" in actions_for_gesture("fist", [], ["Switch app"])
    assert "shortcut:Switch app" in actions_for_gesture("open_palm", [], ["Switch app"])


def test_tampered_shortcut_in_the_config_file_is_refused():
    controller, executor = _controller(mappings={"fist": "shortcut:Bad"}, shortcuts={"Bad": ["command", "power"]})
    with pytest.raises(ShortcutError):
        controller.dispatch(_event("fist"))
    assert executor.calls == []


# -------------------------------------------------- confirmation / practice
class _Hub:
    def __init__(self):
        self.sent = []

    def trigger(self, name):
        self.sent.append(name)
        return True


def test_smart_home_command_waits_for_the_confirm_gesture():
    clock, hub = _Clock(), _Hub()
    controller, _ = _controller(
        mappings={"fist": "smart_home:Lamp", "thumbs_up": "confirm"}, smart_home=hub,
        confirm_smart_home=True, confirm_seconds=5.0, clock=clock)

    assert controller.dispatch(_event("thumbs_up")) is None  # nothing to confirm
    assert controller.dispatch(_event("fist")).label == "Confirm Lamp?"
    assert hub.sent == []
    clock.now += 2
    assert controller.dispatch(_event("thumbs_up")).label == "Lamp"
    assert hub.sent == ["Lamp"]
    assert controller.dispatch(_event("thumbs_up")) is None  # a confirmation is used up

    controller.dispatch(_event("fist"))
    clock.now += 6  # too late
    assert controller.dispatch(_event("thumbs_up")) is None
    assert hub.sent == ["Lamp"]


def test_practice_mode_reports_actions_without_performing_them():
    hub = _Hub()
    controller, executor = _controller(mappings={"pinch": "left_click", "fist": "smart_home:Lamp",
                                                 "open_palm": "move_cursor", "two_fingers": "right_click"},
                                       smart_home=hub)
    controller.dry_run = True
    assert controller.dispatch(_event("pinch")).label == "Left Click"
    assert controller.dispatch(_event("fist")).label == "Lamp"
    assert controller.dispatch(_event("open_palm")).label == "Mouse Move"
    assert executor.calls == [] and hub.sent == []
    controller.dry_run = False
    controller.dispatch(_event("two_fingers"))
    assert executor.calls == [("click", "right")]


# ------------------------------------------------------------------ profiles
def test_builtin_profiles_are_valid_and_differ_where_it_matters():
    presets = builtin_profiles()
    assert list(presets) == ["Default", "Work", "Presentation", "Air Mouse", "Browse", "GestureAI", "JARVIS", "Media"]
    for profile in presets.values():
        assert validate_profile(profile) == profile  # already clean
    assert presets["Presentation"]["mappings"]["swipe_right"] == "next_slide"
    assert presets["Presentation"]["enabled"]["mouse_control"] is False
    assert presets["Media"]["mappings"]["swipe_right"] == "next_track"
    assert presets["Work"]["mappings"]["fist"] == "none" and presets["Work"]["drag_enabled"] is True


def test_profile_store_create_rename_duplicate_delete_and_persist(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    assert store.names() == ["Default", "Work", "Presentation", "Air Mouse", "Browse", "GestureAI", "JARVIS", "Media"]
    store.delete("Air Mouse")
    store.delete("Browse")
    store.delete("GestureAI")

    mine = store.get("Default")
    mine["mappings"]["fist"] = "mute"
    assert store.create("  Video   editing ", mine) == "Video editing"
    with pytest.raises(ProfileError):
        store.create("Video editing", mine)
    with pytest.raises(ProfileError):
        store.create("bad/name", mine)
    assert store.duplicate("Video editing") == "Video editing copy"
    assert store.duplicate("Video editing") == "Video editing copy 2"
    assert store.rename("Work", "Office") == "Office"
    assert store.names()[1] == "Office"  # keeps its position
    with pytest.raises(ProfileError):
        store.rename("Office", "Media")
    store.delete("Video editing copy 2")

    reloaded = ProfileStore(path)
    assert reloaded.names() == ["Default", "Office", "Presentation", "JARVIS", "Media", "Video editing", "Video editing copy"]
    assert reloaded.get("Video editing")["mappings"]["fist"] == "mute"
    assert set(reloaded.get("Media")) == set(PROFILE_KEYS)

    for name in reloaded.names()[1:]:
        reloaded.delete(name)
    with pytest.raises(ProfileError):
        reloaded.delete("Default")  # the last one stays


def test_export_then_import_round_trips(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    file = tmp_path / "media.json"
    store.export_to("Media", file)
    assert store.import_from(file) == "Media 2"
    assert store.get("Media 2") == store.get("Media")


def test_imported_profile_is_sanitised(tmp_path):
    hostile = {
        "touchlessx_profile": 1,
        "name": "../../etc/passwd",
        "mappings": {
            "fist": "shortcut:Wipe disk",       # shortcut that doesn't exist here
            "pinch": "smart_home:Front door",   # device that doesn't exist here
            "two_fingers": "run_shell",         # unknown action
            "open_palm": "left_click",          # allowed: a pose may carry a one-shot action
            "swipe_right": "move_cursor",       # a flick can't be held, so no continuous action
            "point": "app:Calculator",          # application that isn't set up here
            "thumbs_up": "shortcut:Switch app",  # exists locally: kept
            "made_up_gesture": "left_click",
            "swipe_left": 42,
        },
        "sensitivity": {"confidence_threshold": -5, "cursor_smoothing": 99, "pinch_threshold": "0.5",
                        "stable_frames": 3.9, "unknown_setting": 1},
        "enabled": {"smart_home": "yes", "media_control": False, "root_access": True},
        "shortcuts": {"Wipe disk": ["command", "q"]},
        "presentation_keys": "launch",
        "drag_enabled": 1,
    }
    file = tmp_path / "hostile.json"
    file.write_text(json.dumps(hostile))
    store = ProfileStore(tmp_path / "profiles.json")
    name = store.import_from(file, shortcuts=["Switch app"], smart_devices=[])
    profile = store.get(name)

    assert name == "Imported profile"  # the unusable name was replaced
    assert set(profile) == set(PROFILE_KEYS)  # no "shortcuts" (or anything else) smuggled in
    assert profile["mappings"]["fist"] == profile["mappings"]["pinch"] == "none"
    assert profile["mappings"]["two_fingers"] == profile["mappings"]["swipe_right"] == "none"
    assert profile["mappings"]["point"] == "none" and profile["mappings"]["open_palm"] == "left_click"
    assert profile["mappings"]["thumbs_up"] == "shortcut:Switch app"
    assert "made_up_gesture" not in profile["mappings"]
    assert profile["mappings"]["swipe_left"] == DEFAULT_CONFIG["mappings"]["swipe_left"]
    assert profile["sensitivity"]["confidence_threshold"] == 0.3
    assert profile["sensitivity"]["cursor_smoothing"] == 0.9
    assert profile["sensitivity"]["pinch_threshold"] == DEFAULT_CONFIG["sensitivity"]["pinch_threshold"]
    assert profile["sensitivity"]["stable_frames"] == 3 and "unknown_setting" not in profile["sensitivity"]
    assert profile["enabled"]["smart_home"] is False and profile["enabled"]["media_control"] is False
    assert "root_access" not in profile["enabled"]
    assert profile["presentation_keys"] == "arrows" and profile["drag_enabled"] is False


def test_import_rejects_files_that_are_not_profiles(tmp_path):
    store = ProfileStore()
    for content in ("not json", "[1, 2, 3]", '{"mappings": {}}'):
        file = tmp_path / "x.json"
        file.write_text(content)
        with pytest.raises(ProfileError):
            store.import_from(file)
    with pytest.raises(ProfileError):
        store.import_from(tmp_path / "missing.json")


# ------------------------------------------------------------------- history
def test_history_counts_trends_retention_and_clear(tmp_path):
    history = History(tmp_path / "data" / "history.db")
    noon = lambda day: datetime(2026, 10, day, 12).timestamp()
    for day, gesture, action, confidence in (
        (9, "pinch", "Left Click", 95), (9, "pinch", "Left Click", 60), (10, "fist", "Play / Pause", 90),
        (11, "pinch", "Left Click", 99), (11, "swipe_right", "Next Slide", 70),
    ):
        history.record(gesture, action, confidence, ts=noon(day))

    assert history.total() == 5 and history.total(noon(10) - 1) == 3
    assert history.top("action") == [("Left Click", 3), ("Next Slide", 1), ("Play / Pause", 1)]
    assert history.top("gesture", limit=1) == [("pinch", 3)]
    assert history.daily(4, today=date(2026, 10, 11)) == [("Thu", 0), ("Fri", 2), ("Sat", 1), ("Sun", 2)]
    assert history.low_confidence_share() == pytest.approx(40.0)
    with pytest.raises(ValueError):
        history.top("ts; DROP TABLE actions")

    history.close()
    reopened = History(tmp_path / "data" / "history.db")
    assert reopened.total() == 5  # persisted
    reopened.enabled = False
    reopened.record("pinch", "Left Click", 90)
    assert reopened.total() == 5  # switched off: nothing recorded
    reopened.clear()
    assert reopened.total() == 0 and reopened.daily(2) != []


def test_history_prunes_old_rows():
    import time

    history = History()
    history.record("pinch", "Left Click", 90, ts=time.time() - 40 * 86400)
    history.record("pinch", "Left Click", 90)
    history.prune(30)
    assert history.total() == 1


# ------------------------------------------------- scrolling / window gestures
def test_scroll_follows_the_hand_in_both_directions_one_axis_at_a_time():
    controller, executor = _controller(mappings={"open_palm": "scroll"})
    clock = _Clock()
    controller._clock = clock
    labels = []
    for delta in ((0.0, -0.05), (0.0, 0.05), (0.05, 0.0), (-0.05, 0.0),
                  (0.02, -0.05)):   # the last: mostly up, slightly right - vertical only
        clock.now += 1.0  # separate strokes
        labels.append(controller.dispatch(_event("open_palm", delta=delta)).label)
    assert labels == ["Scroll Up", "Scroll Down", "Scroll Right", "Scroll Left", "Scroll Up"]
    assert [call[0] for call in executor.calls] == ["scroll", "scroll", "hscroll", "hscroll", "scroll"]
    assert [call[1] > 0 for call in executor.calls] == [True, False, True, False, True]

    for style in ("follow", "glide"):
        inverted, _ = _controller(mappings={"open_palm": "scroll"}, scroll_style=style)
        inverted.scroll_invert = True
        assert inverted.dispatch(_event("open_palm", delta=(0.0, -0.1))).label == "Scroll Down"
        inverted, _ = _controller(mappings={"open_palm": "scroll"}, scroll_style=style)
        inverted.scroll_invert = True
        assert inverted.dispatch(_event("open_palm", delta=(0.1, 0.0))).label == "Scroll Left"


def test_fist_minimises_and_opening_the_hand_restores():
    controller, executor = _controller(mappings={"fist": "minimize_window", "hand_open": "restore_window"})
    assert controller.dispatch(_event("fist")).label == "Minimize Window"
    assert controller.dispatch(_event("hand_open")).label == "Restore Window"
    executor.can_restore = False  # nothing left to bring back
    assert controller.dispatch(_event("hand_open")) is None
    assert executor.calls == [("minimize",), ("restore",), ("restore",)]


def test_opening_a_held_fist_is_reported_once_as_hand_open():
    engine = GestureEngine(pose_hold_seconds=0.25, stable_frames=2)
    fist, palm = _FakeHandResult(_hand()), _FakeHandResult(
        _hand(index=True, middle=True, ring=True, pinky=True, thumb=True))
    half_open = _FakeHandResult(_hand(index=True, middle=True))  # seen for a frame while opening

    names = [engine.update(fist, now=i * 0.04).name for i in range(10)]            # held 0.4 s
    names += [engine.update(half_open, now=0.40).name]
    events = [engine.update(palm, now=0.44 + i * 0.04) for i in range(4)]
    assert [e.name for e in events] == ["fist", "hand_open", "open_palm", "open_palm"]
    assert events[1].is_trigger is True

    # an open hand that was not a fist a moment ago is just an open hand
    later = [engine.update(palm, now=5.0 + i * 0.04).name for i in range(3)]
    assert "hand_open" not in later

    # a fist that is only flashed (not held) does not count either
    brief = GestureEngine(pose_hold_seconds=0.25)
    brief.update(fist, now=0.0)
    brief.update(fist, now=0.05)
    assert brief.update(palm, now=0.1).name == "open_palm"


def test_muted_motion_gestures_do_not_interrupt_the_pose():
    def run(muted):
        engine = GestureEngine(swipe_min_distance=0.2, swipe_max_duration=0.4)
        engine.muted = muted
        names = []
        for i in range(10):  # a fast sideways move of the open hand
            hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
            hand[:, 0] += 0.06 * i
            names.append(engine.update(_FakeHandResult(hand), now=i * 0.035).name)
        return names

    assert "swipe_right" in run(set())
    assert set(run({"swipe_right"})) == {"open_palm"}  # scrolling carries on uninterrupted

    engine = GestureEngine()
    engine.muted = {"hand_open"}
    engine.update(_FakeHandResult(_hand()), now=0.0)
    palm = _FakeHandResult(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True))
    assert engine.update(palm, now=0.1).name == "open_palm"


def test_browse_profile_is_wired_the_way_it_reads():
    browse = builtin_profiles()["Browse"]
    assert browse["mappings"]["open_palm"] == browse["mappings"]["two_fingers"] == "scroll"
    assert browse["mappings"]["point"] == "move_cursor" and browse["drag_enabled"] is True
    assert (browse["mappings"]["three_fingers"], browse["mappings"]["thumbs_up"]) == ("copy", "paste")
    assert (browse["mappings"]["point_left"], browse["mappings"]["point_right"]) == ("delete", "right_click")
    assert browse["mappings"]["fist"] == "minimize_window" and browse["mappings"]["hand_open"] == "restore_window"
    assert browse["mappings"]["swipe_left"] == browse["mappings"]["swipe_right"] == "none"
    assert validate_profile(browse) == browse

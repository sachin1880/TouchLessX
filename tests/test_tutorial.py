"""Tests for the guided lesson on the Learn page. No camera or display needed."""
from __future__ import annotations

from src.config import DEFAULT_CONFIG
from src.gesture_engine import GestureEvent
from src.tutorial import HOW, TEACHING_ORDER, Lesson, build_steps


def _event(name, trigger=False, hold=0.0, delta=None):
    return GestureEvent(name, 90.0, (0.5, 0.5), delta, trigger, hold)


def _steps(mappings=None, enabled=None, custom=(), emergency="none"):
    mappings = dict(DEFAULT_CONFIG["mappings"] if mappings is None else mappings)
    enabled = {"mouse_control", "media_control", "volume_control"} if enabled is None else enabled
    return build_steps(
        lambda g: mappings.get(g, "none"), lambda a: a, lambda g: "*", lambda g: g,
        lambda c: c in enabled, list(custom), emergency,
    )


def _lesson_at(lesson, key):
    lesson.go([s.key for s in lesson.steps].index(key))
    return lesson


def test_every_taught_gesture_has_instructions():
    assert set(TEACHING_ORDER) <= set(HOW)


def test_the_lesson_covers_only_mapped_gestures_in_teaching_order():
    steps = _steps({"pinch": "left_click", "open_palm": "move_cursor", "fist": "none", "point": "volume"})
    keys = [s.key for s in steps]
    assert keys == ["intro", "open_palm", "point", "pinch", "double_click", "stop", "done"]


def test_goals_follow_the_kind_of_action():
    steps = {s.key: s for s in _steps({"open_palm": "move_cursor", "fist": "play_pause",
                                       "both_palms": "toggle_pause", "two_hand_pinch": "zoom",
                                       "three_fingers": "safety_hold"})}
    assert steps["open_palm"].goal == "move"
    assert steps["two_hand_pinch"].goal == "move"
    assert steps["fist"].goal == "trigger" and steps["fist"].target == 1
    assert steps["both_palms"].goal == "trigger" and steps["both_palms"].target == 2  # pause, then resume
    assert steps["three_fingers"].goal == "hold"


def test_a_switched_off_feature_and_the_emergency_gesture_are_pointed_out():
    steps = {s.key: s for s in _steps({"swipe_right": "next_slide", "fist": "play_pause"}, enabled=set(),
                                      emergency="fist")}
    assert "switched off" in steps["swipe_right"].note
    assert "emergency stop" in steps["fist"].note


def test_custom_gestures_are_taught_after_the_built_in_ones():
    keys = [s.key for s in _steps({"pinch": "copy", "custom:Rock": "paste"}, custom=["custom:Rock"])]
    assert keys.index("custom:Rock") > keys.index("pinch")


def test_a_one_shot_step_finishes_on_the_trigger_only():
    lesson = _lesson_at(Lesson(_steps({"fist": "play_pause"})), "fist")
    assert not lesson.feed(_event("fist", hold=0.2), 1.0)
    assert not lesson.feed(_event("pinch", trigger=True), 1.1)  # another gesture doesn't count
    assert lesson.feed(_event("fist", trigger=True, hold=0.4), 1.2)
    assert "fist" in lesson.completed and lesson.progress == 1.0


def test_double_click_needs_two_pinches_within_the_window():
    lesson = _lesson_at(Lesson(_steps({"pinch": "left_click"}), double_click_seconds=0.8), "double_click")
    assert not lesson.feed(_event("pinch", trigger=True), 1.0)
    assert lesson.progress == 0.5
    lesson.missed(2.5)  # too slow: start again
    assert lesson.progress == 0.0
    assert not lesson.feed(_event("pinch", trigger=True), 3.0)
    assert lesson.feed(_event("pinch", trigger=True), 3.5)


def test_a_movement_step_counts_how_far_the_hand_travels():
    lesson = _lesson_at(Lesson(_steps({"two_hand_pinch": "zoom"})), "two_hand_pinch")
    assert not lesson.feed(_event("two_hand_pinch", delta=(0.1, 0.0)), 1.0)
    assert not lesson.feed(_event("two_hand_pinch", delta=(-0.1, 0.0)), 1.1)
    assert lesson.feed(_event("two_hand_pinch", delta=(0.06, 0.0)), 1.2)


def test_a_hold_step_follows_the_hold_time():
    lesson = _lesson_at(Lesson(_steps({"three_fingers": "safety_hold"})), "three_fingers")
    assert not lesson.feed(_event("three_fingers", hold=0.75), 1.0)
    assert lesson.progress == 0.5
    assert lesson.feed(_event("three_fingers", hold=1.6), 2.0)


def test_information_steps_ignore_gestures_and_moving_resets_progress():
    lesson = Lesson(_steps({"fist": "play_pause"}))
    assert not lesson.feed(_event("fist", trigger=True), 1.0)  # the intro is just reading
    lesson.next()
    lesson.feed(_event("fist", hold=0.1), 1.1)
    lesson.back()
    lesson.next()
    assert lesson.progress == 0.0
    lesson.go(99)
    assert lesson.is_last and lesson.step.key == "done"

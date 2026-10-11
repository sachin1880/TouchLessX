"""Tests for the safety layer and the pieces added for the GestureAI specification:
lock/unlock, emergency stop, pointing gestures, flexible poses, per-gesture settings,
calibration, application launching, and the training pipeline."""
from __future__ import annotations

import numpy as np
import pytest

from src.action_controller import (
    ActionController, AppError, continuous_poses, validate_app_path,
)
from src.gesture_engine import GestureEngine, classify_static_gesture
from src.profiles import builtin_profiles, validate_profile
from src.safety import SafetyGuard
from src.training import (
    GestureDataset, MIN_SAMPLES_TO_TRAIN, ModelManager, TrainedClassifier, TrainingError, train,
)
from tests.test_features import ALL_ON, RecordingExecutor, _Clock, _event
from tests.test_gestures import _FakeHandResult, _hand

SPEC = builtin_profiles()["GestureAI"]["mappings"]


def _controller(mappings=None, **kwargs):
    executor = RecordingExecutor()
    safety = SafetyGuard()
    mappings = dict(SPEC if mappings is None else mappings)
    controller = ActionController(executor, safety, dict(ALL_ON), cursor_smoothing=0.0, mappings=mappings, **kwargs)
    controller.continuous_gestures = continuous_poses(mappings)
    return controller, executor, safety


# ------------------------------------------------------------- lock / unlock
def test_fist_locks_and_only_the_unlock_gesture_gets_through():
    controller, executor, safety = _controller()
    assert controller.dispatch(_event("fist")).label == "Controls Locked"
    assert safety.locked is True

    for name in ("point", "pinch", "two_fingers", "point_right", "open_palm", "three_fingers", "fist"):
        assert controller.dispatch(_event(name, delta=(0.0, 0.1), hold=1.0)) is None
    assert executor.calls == []  # ordinary hand movement did nothing at all

    assert controller.dispatch(_event("thumbs_up")).label == "Controls Unlocked"
    assert safety.locked is False
    controller.dispatch(_event("pinch"))
    assert executor.calls == [("click", "left")]


def test_locking_drops_a_drag_in_progress():
    controller, executor, _ = _controller(drag_enabled=True)
    controller.dispatch(_event("pinch", trigger=False, hold=2.0))  # drag starts
    controller.dispatch(_event("fist"))
    assert executor.calls == [("down",), ("up",)]


def test_low_confidence_cannot_unlock():
    controller, _, safety = _controller()
    controller.dispatch(_event("fist"))
    assert controller.dispatch(_event("thumbs_up", confidence=20)) is None
    assert safety.locked is True


# ------------------------------------------------------------ emergency stop
def test_emergency_gesture_stops_everything_in_any_profile_even_when_locked():
    for profile in builtin_profiles().values():
        controller, executor, safety = _controller(profile["mappings"])
        controller.emergency_gesture = "custom:Stop"
        assert controller.dispatch(_event("custom:Stop")).label == "Emergency Stop"
        assert safety.paused is True
        for name in ("pinch", "fist", "thumbs_up", "open_palm", "point", "custom:Stop"):
            assert controller.dispatch(_event(name, delta=(0.0, 0.1), hold=1.0)) is None
        assert executor.calls == []

    controller, _, safety = _controller()
    controller.emergency_gesture = "custom:Stop"
    controller.dispatch(_event("fist"))  # locked
    assert controller.dispatch(_event("custom:Stop")).label == "Emergency Stop"
    assert safety.paused is True


def test_emergency_stop_on_a_held_pose_needs_a_moment_and_releases_a_drag():
    controller, executor, safety = _controller(drag_enabled=True)
    controller.emergency_gesture = "open_palm"  # a continuous pose in this profile
    controller.dispatch(_event("pinch", trigger=False, hold=2.0))  # dragging
    assert controller.dispatch(_event("open_palm", hold=0.1)) is None  # a glimpse is not enough
    assert safety.paused is False
    assert controller.dispatch(_event("open_palm", hold=0.4)).label == "Emergency Stop"
    assert executor.calls == [("down",), ("up",)]


def test_emergency_stop_action_and_pending_confirmation_are_cleared():
    class Hub:
        sent: list = []

        def trigger(self, name):
            self.sent.append(name)
            return True

    clock = _Clock()
    controller, _, safety = _controller(
        {"pinch": "smart_home:Lamp", "thumbs_up": "confirm", "fist": "emergency_stop"},
        smart_home=Hub(), confirm_smart_home=True, clock=clock)
    controller.dispatch(_event("pinch"))  # armed, waiting for confirmation
    assert controller.dispatch(_event("fist")).label == "Emergency Stop"
    safety.set_paused(False)  # the user resumes from the UI
    assert controller.dispatch(_event("thumbs_up")) is None  # the armed command did not survive
    assert Hub.sent == []


# ----------------------------------------------------------- gestures / poses
def test_sideways_pointing_is_told_apart_from_pointing_up():
    up = _hand(index=True)
    assert classify_static_gesture(up) == "point"
    left, right = _hand(index=True), _hand(index=True)
    left[8] = [0.22, 0.62, 0.0]
    right[8] = [0.82, 0.62, 0.0]
    assert classify_static_gesture(left) == "point_left"
    assert classify_static_gesture(right) == "point_right"


def test_unmapped_sideways_point_still_counts_as_point():
    sideways = _hand(index=True)
    sideways[8] = [0.82, 0.62, 0.0]
    engine = GestureEngine()
    assert engine.update(_FakeHandResult(sideways), now=0.0).name == "point_right"
    engine = GestureEngine()
    engine.muted = {"point_left", "point_right"}
    assert engine.update(_FakeHandResult(sideways), now=0.0).name == "point"  # the cursor keeps moving


def test_a_pose_is_held_or_one_shot_depending_on_its_mapping():
    two = _FakeHandResult(_hand(index=True, middle=True))

    scroll = GestureEngine()
    scroll.continuous_gestures = continuous_poses({"two_fingers": "scroll"})
    assert [scroll.update(two, now=i * 0.04).is_trigger for i in range(3)] == [True, True, True]

    click = GestureEngine()
    click.continuous_gestures = continuous_poses({"two_fingers": "right_click"})
    assert [click.update(two, now=i * 0.04).is_trigger for i in range(3)] == [True, False, False]

    assert continuous_poses(SPEC) == {"open_palm", "point", "two_fingers", "two_hand_pinch"}


def test_spec_profile_behaves_as_specified():
    controller, executor, _ = _controller()
    assert validate_profile(builtin_profiles()["GestureAI"]) == builtin_profiles()["GestureAI"]
    assert controller.dispatch(_event("open_palm", delta=(0.0, 0.2), hold=1.0)).label == "Safety Hold"
    assert controller.dispatch(_event("point", tip=(0.5, 0.5))).label == "Mouse Move"
    assert controller.dispatch(_event("two_fingers", delta=(0.0, -0.05), hold=1.0)).label == "Scroll Up"
    assert controller.dispatch(_event("point_right")).label == "Next Slide"
    assert controller.dispatch(_event("point_left")).label == "Previous Slide"
    assert executor.calls == [("move", 500, 500), ("scroll", 132), ("key", "right"), ("key", "left")]


def test_per_gesture_switch_confidence_and_cooldown():
    controller, executor, _ = _controller({"pinch": "left_click", "two_fingers": "right_click"})
    controller.disabled_gestures = {"two_fingers"}
    controller.gesture_confidence = {"pinch": 0.9}
    assert controller.dispatch(_event("two_fingers")) is None       # switched off
    assert controller.dispatch(_event("pinch", confidence=80)) is None  # under its own threshold
    assert controller.dispatch(_event("pinch", confidence=95)).label == "Left Click"
    assert executor.calls == [("click", "left")]

    engine = GestureEngine(cooldown_seconds=0.2)
    engine.cooldowns = {"fist": 2.0}
    fist, palm = _FakeHandResult(_hand()), _FakeHandResult(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True))
    fired = []
    for start in (0.0, 1.0, 2.5):
        fired.append(engine.update(fist, now=start).is_trigger)
        engine.update(palm, now=start + 0.3)
    assert fired == [True, False, True]  # the second fist fell inside this gesture's own 2 s cooldown


def test_calibrated_reach_maps_the_users_area_onto_the_screen():
    controller, executor, _ = _controller({"open_palm": "move_cursor"})
    controller.calibration = (0.3, 0.4, 0.7, 0.8)
    for point in ((0.3, 0.4), (0.5, 0.6), (0.7, 0.8), (0.9, 0.1)):
        controller.dispatch(_event("open_palm", tip=point))
    assert executor.calls == [("move", 0, 0), ("move", 500, 500), ("move", 999, 999), ("move", 999, 0)]


# ------------------------------------------------------- application launching
def test_only_real_applications_can_be_opened(tmp_path, monkeypatch):
    import src.action_controller as module

    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    app = tmp_path / "Calculator.app"
    app.mkdir()
    script = tmp_path / "evil.sh"
    script.write_text("rm -rf ~")
    assert validate_app_path(app) == str(app)
    for bad in (script, tmp_path / "Missing.app", "/bin/sh", "open -a Calculator; rm -rf ~"):
        with pytest.raises(AppError):
            validate_app_path(bad)

    controller, executor, _ = _controller({"fist": "app:Calc", "pinch": "app:Gone", "two_fingers": "app:Bad"})
    controller.apps = {"Calc": str(app), "Bad": str(script)}
    assert controller.dispatch(_event("fist")).label == "Open Calc"
    assert controller.dispatch(_event("pinch")) is None  # no such application configured
    with pytest.raises(AppError):
        controller.dispatch(_event("two_fingers"))  # tampered config entry is refused
    assert executor.calls == [("open_app", str(app))]

    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    with pytest.raises(AppError):
        validate_app_path(app)


def test_show_app_uses_the_callback_the_ui_registers():
    controller, _, _ = _controller({"three_fingers": "show_app"})
    assert controller.dispatch(_event("three_fingers")) is None  # nothing registered: nothing happens
    shown = []
    controller.callbacks["show_app"] = lambda: shown.append(True)
    assert controller.dispatch(_event("three_fingers")).label == "Show TouchlessX"
    assert shown == [True]


# ------------------------------------------------------------------- training
def _samples(pose: np.ndarray, count: int, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [pose + rng.normal(0, 0.004, (21, 3)) for _ in range(count)]


ROCK = _hand(index=True, pinky=True)
FOUR = _hand(index=True, middle=True, ring=True, pinky=True)
CALL = _hand(pinky=True, thumb=True)


def _dataset(path=None) -> GestureDataset:
    dataset = GestureDataset(path)
    dataset.add_samples("Rock", _samples(ROCK, 40, 1))
    dataset.add_samples("Four", _samples(FOUR, 40, 2))
    dataset.add_samples("Call", _samples(CALL, 40, 3))
    return dataset


def test_dataset_round_trips_through_csv_and_skips_damaged_rows(tmp_path):
    path = tmp_path / "data" / "gesture_samples.csv"
    dataset = _dataset(path)
    lines = path.read_text().splitlines()
    assert lines[0].startswith("label,f0,f1") and len(lines) == 121 and len(lines[1].split(",")) == 43

    with open(path, "a") as f:
        f.write("Rock,1,2,3\n")                                  # too short
        f.write("bad/name," + ",".join(["0.1"] * 42) + "\n")      # invalid label
        f.write("Rock," + ",".join(["nan"] * 42) + "\n")          # not finite
        f.write("Rock," + ",".join(["oops"] * 42) + "\n")         # not numbers
    reloaded = GestureDataset(path)
    assert reloaded.labels() == ["Rock", "Four", "Call"]
    assert [reloaded.count(label) for label in reloaded.labels()] == [40, 40, 40]
    assert np.allclose(reloaded.arrays()[0], dataset.arrays()[0], atol=1e-3)


def test_dataset_labels_can_be_created_renamed_cleared_and_deleted(tmp_path):
    dataset = GestureDataset(tmp_path / "samples.csv")
    assert dataset.create(" Peace ") == "Peace" and dataset.count("Peace") == 0
    with pytest.raises(TrainingError):
        dataset.create("Peace")
    dataset.add_samples("Peace", _samples(ROCK, 12, 0))
    assert dataset.rename("Peace", "Horns") == "Horns"
    dataset.create("Other")
    with pytest.raises(TrainingError):
        dataset.rename("Horns", "Other")
    dataset.clear_samples("Horns")
    assert dataset.count("Horns") == 0
    dataset.delete("Horns")
    assert GestureDataset(tmp_path / "samples.csv").labels() == []  # empty labels hold no rows on disk
    assert dataset.labels() == ["Other"]


def test_dataset_warns_about_thin_and_unbalanced_data():
    dataset = GestureDataset()
    assert "No gestures yet" in dataset.issues()[0]
    dataset.add_samples("Rock", _samples(ROCK, 4, 1))
    assert f"at least {MIN_SAMPLES_TO_TRAIN}" in dataset.issues()[0]
    dataset.add_samples("Rock", _samples(ROCK, 16, 2))
    text = " ".join(dataset.issues())
    assert "only 20 samples" in text and "Only one gesture" in text
    dataset.add_samples("Four", _samples(FOUR, 90, 3))
    assert "unbalanced" in " ".join(dataset.issues())
    assert _dataset().issues() == []


def test_training_reports_metrics_from_a_held_out_split():
    bundle = train(_dataset())
    report = bundle["report"]
    assert report["labels"] == ["Rock", "Four", "Call"]
    assert report["train_size"] == 90 and report["validation_size"] == 30
    assert report["accuracy"] == report["precision"] == report["recall"] == 1.0  # three distinct poses
    assert report["confusion"] == [[10, 0, 0], [0, 10, 0], [0, 0, 10]]
    assert report["per_label"]["Call"] == {"precision": 1.0, "recall": 1.0, "validation_samples": 10}


def test_training_measures_confusion_between_look_alike_gestures():
    dataset = GestureDataset()
    rng = np.random.default_rng(0)
    nearly_rock = ROCK.copy()
    nearly_rock[20] += [0.01, 0.01, 0.0]  # pinky tip a hair off: the two are barely separable
    dataset.add_samples("Rock", [ROCK + rng.normal(0, 0.02, (21, 3)) for _ in range(40)])
    dataset.add_samples("Rock-ish", [nearly_rock + rng.normal(0, 0.02, (21, 3)) for _ in range(40)])
    report = train(dataset)["report"]
    assert 0.2 < report["accuracy"] < 0.95  # the evaluation is honest about it
    assert sum(map(sum, report["confusion"])) == report["validation_size"] == 20


def test_training_refuses_too_little_data_and_handles_a_single_gesture():
    dataset = GestureDataset()
    dataset.add_samples("Rock", _samples(ROCK, 5, 0))
    with pytest.raises(TrainingError):
        train(dataset)
    dataset.add_samples("Rock", _samples(ROCK, 20, 1))
    dataset.add_samples("Tiny", _samples(FOUR, 3, 2))  # too few: left out, not fatal
    bundle = train(dataset)
    assert bundle["report"]["labels"] == ["Rock"] and bundle["report"]["accuracy"] is None
    assert TrainedClassifier(bundle).predict(ROCK) == "Rock"


def test_trained_classifier_names_trained_poses_and_rejects_everything_else():
    classifier = TrainedClassifier(train(_dataset()))
    assert classifier.predict(ROCK) == "Rock"
    assert classifier.predict(FOUR * 0.5 + 0.3) == "Four"  # smaller, elsewhere in the frame
    assert classifier.predict(CALL) == "Call"
    for untrained in (_hand(ring=True), _hand(middle=True, pinky=True), _hand(index=True, ring=True)):
        assert classifier.predict(untrained) is None

    engine = GestureEngine(custom_classifier=classifier)
    assert engine.update(_FakeHandResult(ROCK), now=0.0).name == "custom:Rock"
    assert engine.update(_FakeHandResult(_hand()), now=0.1).name == "fist"  # built-ins still win


def test_models_are_versioned_persisted_and_can_be_reverted(tmp_path):
    folder = tmp_path / "models"
    manager = ModelManager(folder)
    assert manager.load_active() is None

    first = GestureDataset()
    first.add_samples("Rock", _samples(ROCK, 30, 1))
    first.add_samples("Four", _samples(FOUR, 30, 2))
    assert manager.save(train(first)) == 1
    assert manager.save(train(_dataset())) == 2 and manager.active == 2

    reopened = ModelManager(folder)  # as on the next start of the app
    assert reopened.active == 2 and [v["version"] for v in reopened.versions] == [1, 2]
    assert reopened.versions[1]["labels"] == ["Rock", "Four", "Call"] and reopened.versions[1]["accuracy"] == 1.0
    assert reopened.load_active().predict(CALL) == "Call"

    reverted = reopened.activate(1)
    assert reverted.predict(CALL) is None and reverted.predict(ROCK) == "Rock"
    assert ModelManager(folder).active == 1
    with pytest.raises(TrainingError):
        reopened.activate(99)

    for _ in range(6):
        manager.save(train(first))
    assert len(manager.versions) == 5 and not (folder / "gesture_model_v1.joblib").exists()

    (folder / "gesture_model_v8.joblib").write_bytes(b"not a model")
    broken = ModelManager(folder)
    assert broken.active == 8 and broken.load_active() is None  # a damaged file must not crash the app


# ------------------------------------------------------------------ air mouse
def test_air_mouse_profile_is_a_complete_one_handed_mouse():
    """Move, click, double-click, drag and drop, right-click and scroll in one sitting."""
    profile = builtin_profiles()["Air Mouse"]
    assert validate_profile(profile) == profile and profile["drag_enabled"] is True
    assert profile["enabled"] == {"mouse_control": True, "media_control": False, "presentation": False,
                                  "volume_control": False, "smart_home": False, "ai_assistant": False}
    clock = _Clock()
    controller, executor, _ = _controller(profile["mappings"], drag_enabled=True, drag_hold_seconds=0.5, clock=clock)
    controller.enabled = dict(profile["enabled"])
    assert continuous_poses(profile["mappings"]) == {"open_palm", "point", "three_fingers", "two_hand_pinch"}

    def step(name, seconds=1.0, **kwargs):
        clock.now += seconds
        result = controller.dispatch(_event(name, **kwargs))
        return result.label if result else None

    assert step("open_palm", tip=(0.2, 0.2)) == "Mouse Move"
    assert step("point", tip=(0.4, 0.4)) == "Mouse Move"            # a pointing finger steers too
    assert step("pinch") == "Left Click"
    assert step("pinch", seconds=0.3) == "Double Click"              # second pinch soon after
    assert step("pinch", seconds=2.0) == "Left Click"                # press ...
    assert step("pinch", trigger=False, hold=0.6) == "Drag"          # ... and hold: button goes down
    assert step("pinch", trigger=False, hold=0.9, delta=(0.2, 0.1)) is None   # carried with the hand
    assert step("open_palm", tip=(0.6, 0.5)) == "Mouse Move"         # opening the pinch drops it
    assert step("two_fingers") == "Right Click"
    assert step("three_fingers", delta=(0.0, -0.05), hold=1.0) == "Scroll Up"
    assert step("three_fingers", delta=(0.05, 0.0), hold=1.0) == "Scroll Right"
    for resting in ("fist", "thumbs_up", "swipe_left", "swipe_right", "point_left"):
        assert step(resting, delta=(0.3, 0.3)) is None               # nothing else is wired up

    assert executor.calls == [
        ("move", 200, 200), ("move", 400, 400), ("click", "left"), ("click_again",), ("click", "left"),
        ("down",), ("move", 600, 500), ("up",), ("move", 600, 500), ("click", "right"),
        ("scroll", 132), ("hscroll", 132),
    ]


def test_cursor_mapping_follows_a_changed_screen_resolution():
    controller, executor, _ = _controller({"open_palm": "move_cursor"})
    controller.dispatch(_event("open_palm", tip=(0.5, 0.5)))
    executor.refresh_screen_size = lambda: (2000, 1200)   # e.g. an external display was plugged in
    controller.refresh_screen()
    controller.dispatch(_event("open_palm", tip=(1.0, 1.0)))
    controller.dispatch(_event("open_palm", tip=(0.25, 0.5)))
    assert executor.calls == [("move", 500, 500), ("move", 1999, 1199), ("move", 500, 600)]
    controller.refresh_screen()  # unchanged size: nothing to do
    assert (controller.screen_w, controller.screen_h) == (2000, 1200)


# ------------------------------------------------------------------ scrolling
def _scroller(style, **kwargs):
    clock = _Clock()
    controller, executor, _ = _controller({"open_palm": "scroll"}, scroll_style=style, clock=clock, **kwargs)

    def move(dx=0.0, dy=0.0, frames=1, hold=1.0):
        labels = []
        for _ in range(frames):
            clock.now += 1 / 30
            result = controller.dispatch(_event("open_palm", delta=(dx, dy), hold=hold))
            labels.append(result.label if result else None)
        return labels

    return controller, executor, clock, move


def test_glide_keeps_scrolling_while_the_hand_is_held_away_and_stops_in_the_middle():
    _, executor, _, move = _scroller("glide")
    assert move(dy=-0.02, frames=2) == [None, None]          # still inside the neutral zone
    assert move(dy=-0.02, frames=4)[-1] == "Scroll Up"         # 0.12 above where it started
    executor.calls.clear()
    assert set(move(frames=15)) == {"Scroll Up"}               # hand held still up there: keeps going
    held = sum(call[1] for call in executor.calls)
    assert held > 100 and all(call[0] == "scroll" and call[1] > 0 for call in executor.calls)

    executor.calls.clear()
    back = move(dy=0.02, frames=6)                             # hand returns to where it started
    assert back[-1] is None and "Scroll Down" not in back      # the return stroke scrolls nothing back
    assert move(frames=10) == [None] * 10                      # resting in the middle: stopped
    assert all(call[1] > 0 for call in executor.calls)

    assert move(dy=0.03, frames=4)[-1] == "Scroll Down"        # below the middle: the other way


def test_glide_speed_grows_with_distance():
    def scrolled(distance):
        _, executor, _, move = _scroller("glide")
        move(dy=-distance)
        executor.calls.clear()
        move(frames=30)  # one second held at that distance
        return sum(call[1] for call in executor.calls)

    near, far = scrolled(0.08), scrolled(0.25)
    assert 30 < near < 300 and far > 5 * near


def test_glide_locks_onto_one_direction_until_the_hand_returns():
    _, executor, _, move = _scroller("glide")
    move(dy=-0.1)                       # going up
    move(dx=0.04, frames=5)             # hand drifts 0.2 sideways while still raised
    assert {call[0] for call in executor.calls} == {"scroll"}  # never turned into horizontal scrolling
    move(dy=0.1, dx=-0.04 * 5)          # back to the middle
    executor.calls.clear()
    assert move(dx=0.03, frames=4)[-1] == "Scroll Right"       # now a sideways move scrolls sideways
    assert {call[0] for call in executor.calls} == {"hscroll"}


def test_glide_takes_a_fresh_neutral_point_when_the_pose_comes_back():
    _, executor, clock, move = _scroller("glide", engage_seconds=0.3)
    move(dy=-0.2)
    assert move(frames=3)[-1] == "Scroll Up"
    clock.now += 2.0                    # hand did something else for a while, far from the old spot
    executor.calls.clear()
    assert move(frames=5) == [None] * 5 and executor.calls == []  # not still scrolling from before
    assert move(dy=-0.1, hold=0.1) == [None]                      # and not before the pose has settled


def test_follow_ignores_jitter_and_does_not_flip_direction_on_it():
    _, executor, _, move = _scroller("follow")
    rng = np.random.default_rng(3)
    position = np.zeros(2)

    def go_to(target):
        """Moves the hand to `target` plus tracking noise (noise is in the position, so
        it stays bounded - it does not accumulate the way random steps would)."""
        nonlocal position
        noisy = np.asarray(target, dtype=float) + rng.normal(0, 0.0015, 2)
        labels = move(dx=float(noisy[0] - position[0]), dy=float(noisy[1] - position[1]))
        position = noisy
        return labels

    for _ in range(90):                 # three seconds of a hand held still, trembling
        go_to((0.0, 0.0))
    assert executor.calls == []

    labels = []
    for i in range(1, 31):              # a steady upward stroke with the same tremble on top
        labels += go_to((0.0, -0.01 * i))
    assert set(labels) <= {"Scroll Up", None} and labels.count("Scroll Up") > 20
    assert {call[0] for call in executor.calls} == {"scroll"}  # no sideways blips either
    assert all(call[1] > 0 for call in executor.calls)        # and never a step backwards


def test_non_mac_scrolling_carries_fractions_into_whole_wheel_clicks():
    from src.action_controller import PIXELS_PER_WHEEL_CLICK, PyAutoGuiExecutor

    sent = []

    class FakePyAutoGui:
        @staticmethod
        def scroll(clicks): sent.append(("v", clicks))
        @staticmethod
        def hscroll(clicks): sent.append(("h", clicks))

    executor = PyAutoGuiExecutor.__new__(PyAutoGuiExecutor)  # no real display or pyautogui needed
    executor._mac, executor._pyautogui, executor._wheel_remainder = None, FakePyAutoGui, [0.0, 0.0]
    for _ in range(4):
        executor.scroll_pixels(0, int(PIXELS_PER_WHEEL_CLICK / 2))   # four half-clicks up
    executor.scroll_pixels(-int(PIXELS_PER_WHEEL_CLICK * 2), 0)      # two clicks left at once
    assert sent == [("v", 1), ("v", 1), ("h", -2)]


# ------------------------------------------------------------- text / clipboard
def _text_controller(monkeypatch, system):
    import src.action_controller as module

    monkeypatch.setattr(module.platform, "system", lambda: system)
    actions = ("copy", "cut", "paste", "select_all", "undo", "redo", "delete", "page_down", "scroll_top", "scroll_bottom")
    gestures = ("fist", "thumbs_up", "two_fingers", "three_fingers", "point_left", "point_right", "pinch",
                "swipe_up", "swipe_down", "hand_open")
    controller, executor, _ = _controller(dict(zip(gestures, actions)))
    executor.hotkey = lambda keys: executor.calls.append(("hotkey", tuple(keys)))
    labels = [controller.dispatch(_event(g)).label for g in gestures]
    return labels, executor.calls


def test_text_actions_send_the_right_keys_on_each_system(monkeypatch):
    labels, mac = _text_controller(monkeypatch, "Darwin")
    assert labels == ["Copy", "Cut", "Paste", "Select All", "Undo", "Redo", "Delete", "Page Down",
                      "Jump to Top", "Jump to Bottom"]
    assert mac == [
        ("hotkey", ("command", "c")), ("hotkey", ("command", "x")), ("hotkey", ("command", "v")),
        ("hotkey", ("command", "a")), ("hotkey", ("command", "z")), ("hotkey", ("command", "shift", "z")),
        ("key", "backspace"), ("key", "pagedown"), ("hotkey", ("command", "up")), ("hotkey", ("command", "down")),
    ]
    _, windows = _text_controller(monkeypatch, "Windows")
    assert windows[:6] == [
        ("hotkey", ("ctrl", "c")), ("hotkey", ("ctrl", "x")), ("hotkey", ("ctrl", "v")),
        ("hotkey", ("ctrl", "a")), ("hotkey", ("ctrl", "z")), ("hotkey", ("ctrl", "y")),
    ]
    assert windows[8:] == [("hotkey", ("ctrl", "home")), ("hotkey", ("ctrl", "end"))]


def test_text_actions_respect_pause_lock_and_practice_mode():
    controller, executor, safety = _controller({"three_fingers": "copy", "point_left": "delete", "fist": "lock_controls"})
    executor.hotkey = lambda keys: executor.calls.append(("hotkey", tuple(keys)))
    safety.set_paused(True)
    assert controller.dispatch(_event("point_left")) is None
    safety.set_paused(False)
    controller.dispatch(_event("fist"))  # locked
    assert controller.dispatch(_event("point_left")) is None
    safety.set_locked(False)
    controller.dry_run = True
    assert controller.dispatch(_event("point_left")).label == "Delete"  # reported, not sent
    assert executor.calls == []


def test_paste_previous_asks_the_app_and_does_nothing_without_an_earlier_copy():
    controller, _, _ = _controller({"thumbs_up": "paste_previous"})
    assert controller.dispatch(_event("thumbs_up")) is None  # no app hooked up
    answers = iter([False, True])
    controller.callbacks["paste_previous"] = lambda: next(answers)
    assert controller.dispatch(_event("thumbs_up")) is None  # nothing was copied before
    assert controller.dispatch(_event("thumbs_up")).label == "Paste Previous Copy"


def test_clipboard_history_keeps_recent_distinct_text_in_memory():
    from src.clipboard import ClipboardHistory

    history = ClipboardHistory(max_items=3, max_chars=20)
    assert history.previous() is None
    assert history.add("alpha") is True and history.add("alpha") is False  # unchanged clipboard
    assert history.add("beta") and history.add("gamma")
    assert history.items == ["gamma", "beta", "alpha"] and history.previous() == "beta"
    assert history.add("alpha")  # copied again: moves to the front, no duplicate
    assert history.items == ["alpha", "gamma", "beta"]
    assert history.add("delta") and history.items == ["delta", "alpha", "gamma"]  # oldest dropped
    for ignored in ("", "   \n", None, 12345, "x" * 21):
        assert history.add(ignored) is False
    assert ClipboardHistory.preview("one\n  two   three", 9) == "one two …"
    history.clear()
    assert history.items == []


def test_sideways_point_needs_a_clearly_horizontal_finger():
    slanted = _hand(index=True)
    slanted[8] = [0.72, 0.50, 0.0]    # about 53 degrees off vertical: still steering the cursor
    assert classify_static_gesture(slanted) == "point"


# ---------------------------------------------------------------- resting hand
def test_hand_resting_at_the_bottom_edge_controls_nothing():
    """From recorded use: a hand lying near the keyboard, wrist below the picture, was
    read as a fist over and over and kept minimising windows."""
    resting_fist = _hand()
    resting_fist[:, 1] += 0.13                      # wrist at y = 1.03, below the frame
    raised_fist = _hand()                           # wrist at y = 0.90

    engine = GestureEngine(pose_hold_seconds=0.25)
    engine.rest_edge = 0.98
    events = [engine.update(_FakeHandResult(resting_fist), now=i * 0.04) for i in range(30)]
    assert {e.name for e in events} == {"resting"} and not any(e.is_trigger for e in events)

    controller, executor, _ = _controller({"fist": "minimize_window", "hand_open": "restore_window"})
    assert all(controller.dispatch(e) is None for e in events) and executor.calls == []

    raised = [engine.update(_FakeHandResult(raised_fist), now=2.0 + i * 0.04) for i in range(10)]
    assert raised[-1].name == "fist" and sum(e.is_trigger for e in raised) == 1   # a raised fist still works

    # lowering an open hand back to rest must not look like "opening from a fist"
    palm = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
    engine.muted = set()
    engine.update(_FakeHandResult(resting_fist), now=3.0)
    assert engine.update(_FakeHandResult(palm), now=3.1).name == "open_palm"

    off = GestureEngine()                            # rule switched off: old behaviour
    assert off.update(_FakeHandResult(resting_fist), now=0.0).name == "fist"


def test_upright_fist_with_the_thumb_alongside_is_not_a_thumbs_up():
    upright = _hand(thumb=True)
    upright[4] = [0.44, 0.57, 0.0]   # thumb tip up beside the knuckles, but not above the hand
    assert classify_static_gesture(upright) == "fist"
    clear = _hand(thumb=True)
    clear[4] = [0.42, 0.45, 0.0]     # standing well clear of everything else
    assert classify_static_gesture(clear) == "thumbs_up"


def test_one_fist_minimises_one_window_even_if_the_pose_wavers():
    clock = _Clock()
    controller, executor, _ = _controller({"fist": "minimize_window"}, clock=clock)
    assert controller.dispatch(_event("fist")).label == "Minimize Window"
    clock.now += 1.0
    assert controller.dispatch(_event("fist")) is None          # re-detected a second later: ignored
    clock.now += 2.5
    assert controller.dispatch(_event("fist")).label == "Minimize Window"
    assert executor.calls == [("minimize",), ("minimize",)]


# ------------------------------------------------------------ swipe scrolling
def _swiper(**kwargs):
    controller, executor, clock, _ = _scroller("flick", **kwargs)

    def frames(pose, count, dy=0.0, dx=0.0, hold=1.0):
        labels = []
        for _ in range(count):
            clock.now += 1 / 30
            result = controller.dispatch(_event(pose, delta=(dx, dy), hold=hold))
            labels.append(result.label if result else None)
        return [label for label in labels if label]

    scrolled = lambda: (sum(c[1] for c in executor.calls if c[0] == "scroll"),
                        sum(c[1] for c in executor.calls if c[0] == "hscroll"))
    return controller, executor, clock, frames, scrolled


def test_a_quick_swipe_sets_the_page_rolling_and_it_coasts_to_a_stop():
    controller, executor, _, frames, scrolled = _swiper()
    assert frames("open_palm", 6, dy=-0.04) == ["Scroll Up"]   # 1.2 frame heights/s upward: one swipe
    during = scrolled()[0]
    frames("open_palm", 60)                                     # hand now still: the page keeps rolling...
    after = scrolled()[0]
    assert after > during and 500 < after < 1500                # ...roughly a screenful in all
    assert controller._momentum is None                         # ...and has come to rest
    executor.calls.clear()
    frames("open_palm", 30)
    assert executor.calls == []


def test_slow_movement_and_the_hand_coming_back_scroll_nothing():
    _, executor, _, frames, scrolled = _swiper()
    assert frames("open_palm", 30, dy=-0.008) == [] and executor.calls == []   # 0.24/s: far too slow
    assert frames("open_palm", 6, dy=-0.04) == ["Scroll Up"]
    assert frames("open_palm", 6, dy=0.04) == []        # the same hand coming straight back, just as fast
    frames("open_palm", 60)
    assert scrolled()[0] > 0 and all(c[1] > 0 for c in executor.calls)   # nothing ever went downward
    assert frames("open_palm", 6, dy=0.04) == ["Scroll Down"]            # later, a real swipe down counts


def test_repeated_swipes_keep_it_going_and_add_speed():
    def distance(swipes):
        _, _, _, frames, scrolled = _swiper()
        for _ in range(swipes):
            frames("open_palm", 6, dy=-0.04)     # swipe up
            frames("open_palm", 9, dy=0.026)     # hand comes back, a little slower
        frames("open_palm", 90)
        return scrolled()[0]

    one, three = distance(1), distance(3)
    assert three > 2.2 * one


def test_swipe_scrolling_survives_the_pose_blurring_mid_swipe():
    """Recorded use: most fast swipes were classified "unknown" part of the way through."""
    _, _, _, frames, scrolled = _swiper()
    frames("open_palm", 4)                                           # the pose, held
    assert frames("open_palm", 1, dy=-0.04) == []
    assert frames("unknown", 5, dy=-0.04, hold=0.0) == ["Scroll Up"]  # blurred for the rest of the swipe
    assert scrolled()[0] > 0

    _, executor, clock, frames, _ = _swiper()                        # but "unknown" alone never scrolls
    assert frames("unknown", 10, dy=-0.04, hold=0.0) == [] and executor.calls == []
    frames("open_palm", 3)
    clock.now += 2.0                                                 # ...nor long after the pose was last seen
    assert frames("unknown", 10, dy=-0.04, hold=0.0) == []


def test_sticky_scrolling_gives_up_when_the_pose_stays_lost():
    controller, _, _, frames, _ = _swiper()
    frames("open_palm", 4)
    frames("unknown", 45, dy=-0.001, hold=0.0)     # 1.5 s of unreadable hand
    controller._momentum = None
    assert frames("unknown", 6, dy=-0.04, hold=0.0) == []


def test_sideways_swipes_scroll_sideways_unless_switched_off():
    _, _, _, frames, scrolled = _swiper()
    assert frames("open_palm", 6, dx=0.04) == ["Scroll Right"]
    frames("open_palm", 40)
    assert scrolled()[1] > 300 and scrolled()[0] == 0

    _, _, _, frames, scrolled = _swiper()
    assert frames("open_palm", 6, dy=-0.04, dx=0.03) == ["Scroll Up"]   # slanted: still vertical
    frames("open_palm", 40)
    assert scrolled()[1] == 0

    controller, _, _, frames, scrolled = _swiper()
    controller.scroll_vertical_only = True
    assert frames("open_palm", 6, dx=0.04) == []
    frames("open_palm", 40)
    assert scrolled() == (0, 0)


def test_coasting_stops_at_once_on_pause_lock_and_emergency_stop():
    for stop in ("pause", "lock", "release"):
        controller, executor, _, frames, _ = _swiper()
        frames("open_palm", 6, dy=-0.04)
        if stop == "pause":
            controller.safety.set_paused(True)
        elif stop == "lock":
            controller.safety.set_locked(True)
        else:
            controller.release()
        executor.calls.clear()
        frames("open_palm", 20)
        frames("none", 10)
        assert executor.calls == [] and controller._momentum is None, stop


def test_coasting_continues_after_the_hand_leaves():
    _, executor, _, frames, scrolled = _swiper()
    frames("open_palm", 6, dy=-0.04)
    before = scrolled()[0]
    frames("none", 30)                      # hand dropped out of view right after the swipe
    assert scrolled()[0] > before


def test_swipe_direction_can_be_reversed():
    controller, _, _, frames, _ = _swiper()
    controller.scroll_invert = True
    assert frames("open_palm", 6, dy=-0.04) == ["Scroll Down"]


# ------------------------------------------------------------ two-hand gestures
def _two(first, second=None, shift=0.0):
    """A frame with a second hand; `shift` moves the second hand sideways."""
    result = _FakeHandResult(first)
    if second is not None:
        moved = second.copy()
        moved[:, 0] += shift
        result.other = _FakeHandResult(moved)
    return result


PALM = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)


def test_both_palms_fire_once_after_being_held_and_single_hand_gestures_wait():
    engine = GestureEngine(stable_frames=2, pose_hold_seconds=0.25)
    events = [engine.update(_two(PALM, PALM, 0.3), now=i * 0.05) for i in range(20)]   # one second
    assert events[-1].name == "both_palms"
    assert sum(e.is_trigger for e in events) == 1                       # once, not every frame
    assert next(e.hold_time for e in events if e.is_trigger) >= 0.6     # and only after a clear hold
    assert not any(e.name == "open_palm" for e in events)               # the palm alone did not act meanwhile

    brief = GestureEngine(stable_frames=2)
    flash = [brief.update(_two(PALM, PALM, 0.3), now=i * 0.05) for i in range(6)]      # 0.3 s: too short
    assert not any(e.is_trigger for e in flash)


def test_mixed_hands_are_two_hands_and_do_nothing():
    engine = GestureEngine()
    for i in range(10):
        event = engine.update(_two(_hand(pinch=True), _hand(), 0.3), now=i * 0.05)   # one pinches, one is a fist
        assert event.name == "two_hands" and event.is_trigger is False
    controller, executor, _ = _controller({"pinch": "left_click", "fist": "minimize_window"})
    assert controller.dispatch(event) is None and executor.calls == []   # the pinch did not click


def test_two_hand_pinch_reports_hands_moving_apart_and_zooms():
    engine = GestureEngine()
    engine.continuous_gestures = continuous_poses({"two_hand_pinch": "zoom"})
    controller, executor, _ = _controller({"two_hand_pinch": "zoom"}, engage_seconds=0.1)
    controller.continuous_gestures = engine.continuous_gestures
    labels = []
    for i in range(12):                       # hands pinched, spreading 0.02 further apart each frame
        event = engine.update(_two(_hand(pinch=True), _hand(pinch=True), 0.2 + 0.02 * i), now=i * 0.05)
        result = controller.dispatch(event)
        labels.append(result.label if result else None)
    assert event.name == "two_hand_pinch" and event.motion_delta[0] == pytest.approx(0.02, abs=1e-4)
    assert set(labels) == {None, "Zoom In"} and labels.count("Zoom In") >= 2
    for i in range(12, 24):                   # and back together
        result = controller.dispatch(engine.update(
            _two(_hand(pinch=True), _hand(pinch=True), 0.42 - 0.02 * (i - 12)), now=i * 0.05))
        labels.append(result.label if result else None)
    assert "Zoom Out" in labels[12:] and "Zoom In" not in labels[13:]
    assert all(call[0] == "hotkey" for call in executor.calls)


def test_second_hand_is_ignored_when_resting_or_when_two_hand_gestures_are_unused():
    resting = PALM.copy()
    resting[:, 1] += 0.13                      # the other hand lies at the bottom edge
    engine = GestureEngine()
    engine.rest_edge = 0.98
    assert engine.update(_two(PALM, resting, 0.3), now=0.0).name == "open_palm"   # one-hand control carries on

    unused = GestureEngine()
    unused.muted = {"both_palms", "two_hand_pinch"}
    assert unused.update(_two(PALM, PALM, 0.3), now=0.0).name == "open_palm"

    only_zoom = GestureEngine()
    only_zoom.muted = {"both_palms"}           # both palms unmapped, zoom still in use
    assert only_zoom.update(_two(PALM, PALM, 0.3), now=0.0).name == "two_hands"


def test_leaving_two_hand_mode_does_not_jump_the_cursor_or_look_like_a_swipe():
    engine = GestureEngine()
    engine.update(_FakeHandResult(PALM), now=0.0)
    for i in range(1, 6):
        engine.update(_two(PALM, PALM, 0.3), now=i * 0.05)
    far = PALM.copy()
    far[:, 0] += 0.3                           # the remaining hand is now somewhere else entirely
    event = engine.update(_FakeHandResult(far), now=0.3)
    assert event.name == "open_palm" and event.motion_delta is None


def test_both_palms_pause_and_resume_by_default():
    from src.config import DEFAULT_MAPPINGS

    assert DEFAULT_MAPPINGS["both_palms"] == "toggle_pause" and DEFAULT_MAPPINGS["two_hand_pinch"] == "zoom"
    controller, _, safety = _controller(dict(DEFAULT_MAPPINGS))
    assert controller.dispatch(_event("both_palms")).label == "Control Paused" and safety.paused
    assert controller.dispatch(_event("both_palms")).label == "Control Resumed" and not safety.paused

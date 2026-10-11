"""Tests for the pieces added after the first MVP: scroll, drag, configurable mappings,
pause-by-gesture, config loading, custom gestures, smart-home webhooks and session stats.

Like the other test modules, none of this needs a camera, display or real input device.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from src.action_controller import ActionController, actions_for_gesture
from src.action_log import ActionLog, SessionStats, build_suggestions
from src.config import DEFAULT_CONFIG, load_config, save_config
from src.custom_gestures import CustomGestureError, CustomGestureStore
from src.gesture_engine import GestureEngine, GestureEvent, classify_static_gesture
from src.safety import SafetyGuard
from src.smart_home import SmartHomeClient, SmartHomeError
from tests.test_gestures import _FakeHandResult, _hand

ALL_ON = {"mouse_control": True, "media_control": True, "presentation": True, "volume_control": True,
          "smart_home": True}


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.screen_size = (1000, 1000)

    def move_mouse(self, x, y): self.calls.append(("move", x, y))
    def click(self, button="left"): self.calls.append(("click", button))
    def double_click(self): self.calls.append(("double_click",))
    def click_again(self): self.calls.append(("click_again",))
    def press_key(self, key): self.calls.append(("key", key))
    def scroll(self, clicks): self.calls.append(("scroll", clicks))
    def hotkey(self, keys): self.calls.append(("hotkey", tuple(keys)))
    def hscroll(self, clicks): self.calls.append(("hscroll", clicks))
    def scroll_pixels(self, dx, dy): self.calls.append(("scroll", dy) if dy else ("hscroll", dx))
    def minimize_window(self): self.calls.append(("minimize",)); return True
    def restore_window(self): self.calls.append(("restore",)); return self.can_restore
    def open_app(self, path): self.calls.append(("open_app", path))
    can_restore = True
    def mouse_down(self): self.calls.append(("down",))
    def mouse_up(self): self.calls.append(("up",))


def _controller(**kwargs):
    executor = RecordingExecutor()
    safety = SafetyGuard(confidence_threshold=0.6)
    controller = ActionController(executor, safety, dict(ALL_ON), cursor_smoothing=0.0, **kwargs)
    return controller, executor, safety


def _event(name, trigger=True, tip=(0.5, 0.5), delta=None, hold=0.0, confidence=90):
    return GestureEvent(name, confidence, tip, delta, trigger, hold)


# ------------------------------------------------------------------ gestures
def test_three_fingers_is_index_middle_ring():
    assert classify_static_gesture(_hand(index=True, middle=True, ring=True)) == "three_fingers"


def test_hold_time_grows_while_a_gesture_is_held():
    engine = GestureEngine()
    pinch = _FakeHandResult(_hand(pinch=True))
    assert engine.update(pinch, now=1.0).hold_time == 0.0
    assert engine.update(pinch, now=1.7).hold_time == pytest.approx(0.7)


def test_swipe_fires_once_for_one_sideways_movement():
    engine = GestureEngine(swipe_min_distance=0.15, cooldown_seconds=0.6)
    triggers = []
    for i in range(12):  # hand sweeps right across the frame in ~0.4s, then keeps going
        hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
        hand[:, 0] += -0.25 + 0.05 * i
        event = engine.update(_FakeHandResult(hand), now=i * 0.035)
        if event.is_trigger and event.name.startswith("swipe"):
            triggers.append(event.name)
    assert triggers == ["swipe_right"]


def _fist_then(engine, pose, start, frames=3, step=0.04):
    """Feeds `frames` frames of a pose; returns the events."""
    return [engine.update(_FakeHandResult(pose), now=start + i * step) for i in range(frames)]


def test_pose_seen_for_a_single_frame_is_ignored():
    # Closing an open hand passes through "two fingers" / "point" for a frame.
    engine = GestureEngine(stable_frames=2)
    palm = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
    _fist_then(engine, palm, 0.0)
    passing = engine.update(_FakeHandResult(_hand(index=True, middle=True)), now=0.12)
    assert passing.name == "open_palm"  # one frame of another pose changes nothing
    settled = _fist_then(engine, _hand(), 0.16)
    assert [e.name for e in settled] == ["open_palm", "fist", "fist"]


def test_one_shot_pose_must_be_held_before_it_fires_but_pinch_is_instant():
    engine = GestureEngine(pose_hold_seconds=0.25)
    fist = _FakeHandResult(_hand())
    assert engine.update(fist, now=0.0).is_trigger is False
    assert engine.update(fist, now=0.1).is_trigger is False
    assert engine.update(fist, now=0.3).is_trigger is True   # held long enough
    assert engine.update(fist, now=0.4).is_trigger is False  # and only once

    brief = GestureEngine(pose_hold_seconds=0.25)
    brief.update(fist, now=0.0)
    assert brief.update(_FakeHandResult(_hand(pinch=True)), now=0.1).is_trigger is True  # pinch: no wait
    assert brief.update(fist, now=0.2).is_trigger is False  # the earlier brief fist never fired


def test_pinch_does_not_flicker_at_the_threshold():
    engine = GestureEngine(pinch_threshold=0.4)
    def gap(distance):  # thumb-index gap in palm lengths
        hand = _hand(pinch=True)
        scale = float(np.hypot(*(hand[9, :2] - hand[0, :2])))
        hand[4] = [hand[8][0] + distance * scale, hand[8][1], 0.0]
        return _FakeHandResult(hand)
    names = [engine.update(gap(d), now=i * 0.04).name for i, d in enumerate([0.38, 0.43, 0.38, 0.45, 0.60])]
    assert names[:4] == ["pinch", "pinch", "pinch", "pinch"] and names[4] != "pinch"
    assert sum(engine.update(gap(0.3), now=1 + i * 0.04).is_trigger for i in range(5)) == 1


def test_two_quick_pinches_both_fire_for_a_double_click():
    engine = GestureEngine(cooldown_seconds=0.6, stable_frames=2)
    pinch, palm = _hand(pinch=True), _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
    fired = []
    for start, pose in ((0.0, palm), (0.12, pinch), (0.24, palm), (0.36, pinch), (0.48, palm)):
        fired += [e.name for e in _fist_then(engine, pose, start) if e.is_trigger and e.name == "pinch"]
    assert fired == ["pinch", "pinch"]


def test_cursor_is_steered_by_the_palm_so_pinching_does_not_move_it():
    engine = GestureEngine()
    open_hand = engine.update(_FakeHandResult(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True)), 0.0)
    pinching = engine.update(_FakeHandResult(_hand(pinch=True)), 0.04)
    assert open_hand.palm == pytest.approx(pinching.palm)
    assert pinching.motion_delta == pytest.approx((0.0, 0.0))
    assert open_hand.index_tip != pinching.index_tip  # the fingertip did move


def test_slow_cursor_sweep_is_not_a_swipe():
    engine = GestureEngine(swipe_min_distance=0.22, swipe_max_duration=0.35)
    names = set()
    for i in range(40):  # crosses 60% of the frame in 1.6 s
        hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb=True)
        hand[:, 0] += -0.3 + 0.015 * i
        names.add(engine.update(_FakeHandResult(hand), now=i * 0.04).name)
    assert names == {"open_palm"}


# ------------------------------------------------------------------- actions
class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_second_quick_click_completes_a_double_click():
    clock = _Clock()
    controller, executor, _ = _controller(clock=clock, double_click_seconds=0.6)
    assert controller.dispatch(_event("pinch")).label == "Left Click"
    clock.now += 0.4
    assert controller.dispatch(_event("pinch")).label == "Double Click"
    clock.now += 0.4
    assert controller.dispatch(_event("pinch")).label == "Left Click"  # a third starts over
    clock.now += 2.0
    assert controller.dispatch(_event("pinch")).label == "Left Click"  # too slow to pair up
    assert executor.calls == [("click", "left"), ("click_again",), ("click", "left"), ("click", "left")]


def test_cursor_filter_steadies_a_resting_hand_but_follows_a_fast_one():
    clock = _Clock()
    executor = RecordingExecutor()
    controller = ActionController(executor, SafetyGuard(), dict(ALL_ON), cursor_smoothing=0.5, clock=clock)
    rng = np.random.default_rng(1)
    for _ in range(60):  # a hand held still, with tracking noise of ~0.4% of the frame
        clock.now += 1 / 30
        noisy = (0.5 + rng.normal(0, 0.004), 0.5 + rng.normal(0, 0.004))
        controller.dispatch(GestureEvent("open_palm", 90, noisy, None, True, 0.0, noisy))
    xs = [c[1] for c in executor.calls[20:]]
    assert max(xs) - min(xs) <= 6  # raw noise would wander ~±12 px on this 1000 px screen

    for i in range(10):  # then a fast move right
        clock.now += 1 / 30
        point = (0.5 + 0.03 * (i + 1), 0.5)
        controller.dispatch(GestureEvent("open_palm", 90, point, None, True, 0.0, point))
    assert executor.calls[-1][1] >= 770  # target 800: lag stays small


def test_cursor_jumps_to_a_hand_that_reappears_elsewhere():
    clock = _Clock()
    controller, executor, _ = _controller(clock=clock)
    controller.cursor_smoothing = 0.8
    controller.dispatch(_event("open_palm", tip=(0.2, 0.2)))
    clock.now += 2.0
    controller.dispatch(_event("open_palm", tip=(0.8, 0.8)))
    assert executor.calls == [("move", 200, 200), ("move", 800, 800)]


def test_scroll_and_volume_wait_for_the_pose_to_settle():
    controller, executor, _ = _controller(engage_seconds=0.3)
    assert controller.dispatch(_event("point", delta=(0.0, -0.1), hold=0.1)) is None
    assert controller.dispatch(_event("three_fingers", delta=(0.0, -0.1), hold=0.1)) is None
    assert executor.calls == []
    assert controller.dispatch(_event("three_fingers", delta=(0.0, -0.1), hold=0.5)).label == "Scroll Up"
    executor.calls.clear()
    assert controller.dispatch(_event("point", delta=(0.0, -0.065), hold=0.5)).label == "Volume Up"
    assert controller.dispatch(_event("point", delta=(0.0, 0.01), hold=0.6)) is None  # under one step
    assert controller.dispatch(_event("point", delta=(0.0, 0.03), hold=0.7)).label == "Volume Down"
    assert executor.calls == [("key", "volumeup"), ("key", "volumeup"), ("key", "volumedown")]


def test_scroll_follows_vertical_hand_movement():
    controller, executor, _ = _controller()  # "follow" style: 3000 px per frame height, 0.006 slack
    assert controller.dispatch(_event("three_fingers", delta=(0.0, -0.05))).label == "Scroll Up"
    assert controller.dispatch(_event("three_fingers", delta=(0.0, 0.05))).label == "Scroll Down"
    assert executor.calls == [("scroll", 132), ("scroll", -114)]


def test_scroll_ignores_jitter():
    controller, executor, _ = _controller()
    assert controller.dispatch(_event("three_fingers", delta=(0.0, 0.001))) is None
    assert executor.calls == []


def test_remapped_gesture_runs_the_new_action():
    controller, executor, _ = _controller(mappings={"fist": "next_track", "pinch": "double_click"})
    assert controller.dispatch(_event("fist")).label == "Next Track"
    controller.dispatch(_event("pinch"))
    assert executor.calls == [("key", "nexttrack"), ("double_click",)]


def test_unmapped_or_invalid_mapping_does_nothing():
    controller, executor, _ = _controller(mappings={"fist": "launch_missiles"})
    assert controller.dispatch(_event("fist")) is None
    assert controller.dispatch(_event("pinch")) is None
    assert executor.calls == []


def test_held_pose_cannot_fire_a_one_shot_action_every_frame():
    controller, executor, _ = _controller(mappings={"open_palm": "left_click"})
    assert controller.dispatch(_event("open_palm")) is None
    assert executor.calls == []


def test_page_keys_for_slides():
    controller, executor, _ = _controller(presentation_keys="page")
    controller.dispatch(_event("swipe_right"))
    controller.dispatch(_event("swipe_left"))
    assert executor.calls == [("key", "pagedown"), ("key", "pageup")]


def test_dead_zone_swallows_small_cursor_movement():
    controller, executor, _ = _controller(cursor_dead_zone=0.01)
    controller.dispatch(_event("open_palm", tip=(0.500, 0.5)))
    controller.dispatch(_event("open_palm", tip=(0.504, 0.5)))  # jitter
    controller.dispatch(_event("open_palm", tip=(0.600, 0.5)))  # real movement
    assert executor.calls == [("move", 500, 500), ("move", 600, 500)]


def test_reach_margin_maps_inner_area_to_full_screen():
    controller, executor, _ = _controller(active_area_margin=0.2)
    controller.dispatch(_event("open_palm", tip=(0.2, 0.8)))
    controller.dispatch(_event("open_palm", tip=(0.5, 0.5)))
    assert executor.calls == [("move", 0, 999), ("move", 500, 500)]


def test_pause_gesture_toggles_and_still_works_while_paused():
    controller, executor, safety = _controller(mappings={"fist": "toggle_pause", "pinch": "left_click"})
    assert controller.dispatch(_event("fist")).label == "Control Paused"
    assert safety.paused is True
    assert controller.dispatch(_event("pinch")) is None  # blocked while paused
    assert controller.dispatch(_event("fist")).label == "Control Resumed"
    controller.dispatch(_event("pinch"))
    assert executor.calls == [("click", "left")]


def test_drag_presses_after_hold_follows_hand_and_releases():
    controller, executor, _ = _controller(drag_enabled=True, drag_hold_seconds=0.5)
    controller.dispatch(_event("open_palm", tip=(0.5, 0.5)))
    controller.dispatch(_event("pinch"))  # the click
    assert controller.dispatch(_event("pinch", trigger=False, hold=0.2)) is None  # not held long enough
    assert controller.dispatch(_event("pinch", trigger=False, hold=0.6)).label == "Drag"
    controller.dispatch(_event("pinch", trigger=False, hold=0.7, delta=(0.1, 0.0)))
    controller.dispatch(_event("open_palm", tip=(0.6, 0.5)))  # pinch opened
    assert executor.calls == [
        ("move", 500, 500), ("click", "left"), ("down",), ("move", 600, 500), ("up",), ("move", 600, 500),
    ]


def test_drag_is_off_by_default_and_released_on_pause():
    controller, executor, safety = _controller()
    controller.dispatch(_event("pinch", trigger=False, hold=2.0))
    assert executor.calls == []

    controller, executor, safety = _controller(drag_enabled=True)
    controller.dispatch(_event("pinch", trigger=False, hold=2.0))
    safety.set_paused(True)
    controller.dispatch(_event("pinch", trigger=False, hold=2.1))
    assert executor.calls == [("down",), ("up",)]


def test_test_buttons_can_force_an_action_past_a_disabled_category():
    controller, executor, _ = _controller()
    controller.enabled["media_control"] = False
    assert controller.run_action("play_pause") is None
    assert controller.run_action("play_pause", force=True).label == "Play / Pause"
    assert executor.calls == [("key", "playpause")]


def test_only_compatible_actions_are_offered_per_gesture():
    pose = actions_for_gesture("open_palm", ["Lamp"])  # a pose can be held or used as a one-shot
    assert "move_cursor" in pose and "left_click" in pose and "smart_home:Lamp" in pose
    for momentary in ("pinch", "swipe_left", "hand_open", "custom:Thumbs up"):
        offered = actions_for_gesture(momentary, ["Lamp"])
        assert "left_click" in offered and "smart_home:Lamp" in offered
        assert "move_cursor" not in offered and "scroll" not in offered


# -------------------------------------------------------------------- config
def test_load_config_fills_in_keys_missing_from_an_old_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"camera_index": 2, "sensitivity": {"pinch_threshold": 0.3},
                                "mappings": {"fist": "mute"}}))
    config = load_config(path)
    assert config["camera_index"] == 2
    assert config["sensitivity"]["pinch_threshold"] == 0.3
    assert config["sensitivity"]["scroll_speed"] == DEFAULT_CONFIG["sensitivity"]["scroll_speed"]
    assert config["mappings"]["fist"] == "mute" and config["mappings"]["pinch"] == "left_click"
    assert DEFAULT_CONFIG["mappings"]["fist"] == "play_pause"  # defaults were not mutated


def test_load_config_survives_missing_or_corrupt_file(tmp_path):
    assert load_config(tmp_path / "nope.json") == DEFAULT_CONFIG
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_config(bad) == DEFAULT_CONFIG
    assert save_config(tmp_path / "out.json", DEFAULT_CONFIG) is True
    assert load_config(tmp_path / "out.json") == DEFAULT_CONFIG


# ----------------------------------------------------------- custom gestures
def _thumbs_up() -> np.ndarray:
    hand = _hand(thumb=True)
    hand[4] = [0.30, 0.55, 0.0]
    return hand


def _rock() -> np.ndarray:
    return _hand(index=True, pinky=True)


def test_custom_gesture_is_recognised_wherever_the_hand_is(tmp_path):
    store = CustomGestureStore(tmp_path / "custom.json")
    rng = np.random.default_rng(0)
    store.add_samples("Thumbs up", [_thumbs_up() + rng.normal(0, 0.002, (21, 3)) for _ in range(12)])
    store.add_samples("Rock", [_rock() + rng.normal(0, 0.002, (21, 3)) for _ in range(12)])

    moved = _thumbs_up() * 0.6 + 0.2  # smaller and elsewhere in the frame
    assert store.predict(moved) == "Thumbs up"
    angle = np.radians(25)  # tilted hand
    turn = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    tilted = _thumbs_up()
    tilted[:, :2] = (tilted[:, :2] - 0.5) @ turn.T + 0.5
    assert store.predict(tilted) == "Thumbs up"
    assert store.predict(_rock()) == "Rock"
    assert store.predict(_hand(ring=True)) is None  # an untrained pose

    reloaded = CustomGestureStore(tmp_path / "custom.json")
    assert reloaded.names() == ["Thumbs up", "Rock"] and reloaded.count("Rock") == 12
    reloaded.remove("Rock")
    assert CustomGestureStore(tmp_path / "custom.json").names() == ["Thumbs up"]


def test_custom_gesture_names_are_validated():
    store = CustomGestureStore()
    with pytest.raises(CustomGestureError):
        store.add_samples("  ", [_rock()])
    with pytest.raises(CustomGestureError):
        store.add_samples("bad/name", [_rock()])
    with pytest.raises(CustomGestureError):
        store.add_samples("Rock", [])


def test_engine_reports_custom_gesture_once_and_only_when_enabled():
    store = CustomGestureStore()
    store.add_samples("Rock", [_rock()] * 5)
    engine = GestureEngine(custom_classifier=store)
    rock = _FakeHandResult(_rock())

    first, second = engine.update(rock, now=0.0), engine.update(rock, now=0.1)
    assert first.name == "custom:Rock" and first.is_trigger is True
    assert second.name == "custom:Rock" and second.is_trigger is False
    # built-in poses always win over trained ones
    assert engine.update(_FakeHandResult(_hand()), now=0.2).name == "fist"

    engine.custom_enabled = False
    assert engine.update(rock, now=2.0).name == "unknown"


# ---------------------------------------------------------------- smart home
class _Response:
    status = 200

    def __enter__(self): return self
    def __exit__(self, *_): return False


def test_smart_home_posts_to_the_webhook_with_token(tmp_path):
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return _Response()

    client = SmartHomeClient(tmp_path / "smart_home.json", opener=opener)
    client.add_device("Lamp", "https://hub.local/api/webhook/lamp", "secret")
    assert client.trigger("Lamp", wait=True) is True
    assert client.trigger("Nope", wait=True) is False

    (request,) = requests
    assert request.full_url == "https://hub.local/api/webhook/lamp"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {"source": "touchlessx", "device": "Lamp"}
    assert client.last_status["Lamp"] == "OK (200)"
    assert SmartHomeClient(tmp_path / "smart_home.json").names() == ["Lamp"]


def test_smart_home_rejects_bad_devices_and_reports_failures():
    def failing(request, timeout):
        raise OSError("hub unreachable")

    client = SmartHomeClient(opener=failing)
    for name, url in (("", "https://x.test"), ("Lamp", "file:///etc/passwd"), ("Lamp", "not a url")):
        with pytest.raises(SmartHomeError):
            client.add_device(name, url)
    client.add_device("Lamp", "http://x.test/hook")
    with pytest.raises(SmartHomeError):
        client.add_device("Lamp", "http://x.test/other")
    client.trigger("Lamp", wait=True)
    assert client.last_status["Lamp"].startswith("Failed")


def test_smart_home_action_needs_the_category_enabled():
    sent = []

    class Hub:
        def trigger(self, name):
            sent.append(name)
            return True

    controller, _, _ = _controller(mappings={"fist": "smart_home:Lamp"}, smart_home=Hub())
    assert controller.dispatch(_event("fist")).label == "Lamp"
    controller.enabled["smart_home"] = False
    assert controller.dispatch(_event("fist")) is None
    assert sent == ["Lamp"]


# ------------------------------------------------------------- log and stats
def test_action_log_writes_text_lines_and_respects_the_switch(tmp_path):
    log = ActionLog(tmp_path / "logs" / "actions.log")
    log.record("pinch", "Left Click", 93.4)
    log.enabled = False
    log.record("fist", "Play / Pause", 80)
    lines = (tmp_path / "logs" / "actions.log").read_text().splitlines()
    assert len(lines) == 1 and "gesture=pinch" in lines[0] and "action=Left Click" in lines[0]
    log.clear()
    assert not (tmp_path / "logs" / "actions.log").exists()


def test_session_stats_and_suggestions():
    stats = SessionStats()
    for i in range(100):
        name = "unknown" if i < 60 else "open_palm"
        stats.record_frame(_event(name, confidence=50), latency_ms=10.0, fps=30.0, now=100 + i / 30)
    stats.record_frame(_event("pinch", confidence=50), 10.0, 30.0, now=104.0)
    stats.record_frame(_event("none", trigger=False, confidence=0), 10.0, 30.0, now=104.1)

    assert stats.hand_frames == 101 and stats.frames == 102
    assert stats.total_gestures == 2 and stats.gesture_counts["pinch"] == 1
    assert stats.accuracy == pytest.approx(100 * 41 / 101)
    assert stats.avg_latency_ms == pytest.approx(10.0)
    assert list(stats.timeline)[:3] == [50.0, 50.0, 50.0]

    tips = " ".join(build_suggestions(stats, camera_ok=True, input_ok=False, paused=True))
    for expected in ("not permitted", "paused", "confidence is averaging 50%", "matched a known gesture"):
        assert expected in tips
    assert build_suggestions(SessionStats(), camera_ok=False, input_ok=True, paused=False)[0].startswith(
        "The camera isn't running")


def test_frame_log_stores_landmarks_and_labels_but_respects_the_switch(tmp_path):
    from src.action_log import FrameLog

    log = FrameLog(tmp_path / "logs" / "frames.csv")
    hand = _hand(pinch=True)
    log.record(_event("pinch"), "pinch", hand, 29.6, 0.12, "Left Click")
    log.record(_event("none", trigger=False), "none", None, 30.0, 0.0)  # no hand: nothing to store
    log.enabled = False
    log.record(_event("fist"), "fist", hand, 30.0, 0.5)
    log.enabled = True
    log.flush()
    header, row = (tmp_path / "logs" / "frames.csv").read_text().splitlines()
    assert header.split(",")[:9] == FrameLog.HEADER[:9] and len(header.split(",")) == 9 + 42
    fields = row.split(",")
    assert fields[2:5] == ["pinch", "pinch", "1"] and fields[7:9] == ["0.120", "Left Click"]
    assert [float(v) for v in fields[9:11]] == [0.5, 0.9]  # the wrist, as x,y
    log.clear()
    assert not (tmp_path / "logs" / "frames.csv").exists()


def test_accent_colours_are_validated_and_everything_else_is_derived():
    from src.colors import ACCENT_PRESETS, accent_palette, contrast, mix, normalize_hex, text_on

    assert normalize_hex(" #2F7DF6 ") == normalize_hex("2f7df6") == "#2f7df6"
    assert normalize_hex("#fa0") == "#ffaa00"
    for bad in ("", "blue", "#12345", "#12345g", "rgb(1,2,3)", None):
        with pytest.raises(ValueError):
            normalize_hex(bad)
    assert mix("#000000", "#ffffff", 0.5) == "#808080" and mix("#123456", "#ffffff", 0) == "#123456"

    assert text_on("#ffd400") == "#10151f"   # yellow needs dark text
    assert text_on("#1d2a6b") == "#ffffff"   # navy needs light text
    for name, color in ACCENT_PRESETS.items():
        palette = accent_palette(color)
        assert palette["accent"] == (color, color), name
        assert contrast(palette["on"][0], color) >= 3.0, name          # button labels stay readable
        assert palette["hover"][0] != color                               # hovering is visible
        assert contrast(palette["soft"][0], "#ffffff") < 1.6             # soft tints stay near the surface...
        assert contrast(palette["soft"][1], "#0f182b") < 2.2             # ...in both themes

"""Tests for Face Control: head steering, winks, expressions, scroll mode and pause,
built from synthetic face landmarks and expression scores. No camera, model or display."""
from __future__ import annotations

import numpy as np

from src.face_control import (
    EYE_A, EYE_B, LIP_LOWER, LIP_UPPER, NOSE_TIP, FaceDispatcher, FaceEngine, FaceResult, eye_openness,
    head_direction,
)
from tests.test_features import _controller

STEP = 1 / 30


def _face(nose=(0.5, 0.5), left=0.3, right=0.3, mouth=0.0, **scores):
    """A face in the middle of the frame. `left` / `right` are the user's eyes' openness
    (lid gap per eye width); frames are mirrored, so the user's left eye is the one
    further left in the image. The eyes are 0.11 apart, centred on (0.5, 0.45)."""
    lm = np.zeros((478, 3), dtype=np.float32)
    lm[NOSE_TIP, :2] = nose
    for eye, x0, opening in ((EYE_A, 0.42, left), (EYE_B, 0.53, right)):
        corner_a, corner_b, upper, lower = eye
        lm[corner_a, :2], lm[corner_b, :2] = (x0, 0.45), (x0 + 0.05, 0.45)
        gap = opening * 0.05
        for k, (u, d) in enumerate(zip(upper, lower)):
            x = x0 + 0.0125 * (k + 1)
            lm[u, :2], lm[d, :2] = (x, 0.45 - gap / 2), (x, 0.45 + gap / 2)
    lm[LIP_UPPER, :2] = (0.5, 0.58)
    lm[LIP_LOWER, :2] = (0.5, 0.58 + mouth)
    names = {"brows": "browInnerUp", "pucker": "mouthPucker", "jaw": "jawOpen", "smile": "mouthSmileLeft"}
    return FaceResult(lm, {names[k]: v for k, v in scores.items()})


def _ready_engine(**kwargs):
    """An engine past its centre calibration, at t = 0.8."""
    engine = FaceEngine(**kwargs)
    for i in range(9):
        engine.update(_face(), i * 0.1)
    return engine


def _run(engine, face, start, seconds):
    events, t = [], start
    while t < start + seconds:
        events.append(engine.update(face, t))
        t += STEP
    return events, t


def _fired(events):
    return [(e.name, e.action) for e in events if e.trigger]


# ------------------------------------------------------------------ measuring
def test_eye_openness_is_the_lid_gap_per_eye_width():
    assert abs(eye_openness(_face(left=0.3).landmarks, EYE_A) - 0.3) < 1e-3
    assert eye_openness(_face(left=0.0).landmarks, EYE_A) < 1e-3


def test_head_direction_follows_the_nose_and_ignores_the_mouth():
    x, y = head_direction(_face(nose=(0.511, 0.5)).landmarks)
    assert abs(x - 0.1) < 1e-3 and abs(y - 0.4545) < 1e-3
    assert head_direction(_face(mouth=0.1).landmarks) == head_direction(_face().landmarks)


def test_the_first_moments_find_the_centre_then_the_head_steers():
    engine = FaceEngine()
    assert engine.update(_face(), 0.0).calibrating
    events, _ = _run(engine, _face(), 0.0, 0.8)
    assert not events[-1].calibrating and events[-1].steer
    assert abs(engine.neutral[1] - 0.4545) < 1e-3


# ------------------------------------------------------------------ eyes
def test_an_ordinary_blink_does_nothing():
    engine = _ready_engine()
    events, t = _run(engine, _face(left=0.03, right=0.03), 1.0, 0.2)
    events += _run(engine, _face(), t, 0.3)[0]
    assert _fired(events) == []


def test_a_left_wink_clicks_once_and_holds_the_cursor():
    engine = _ready_engine()
    events, _ = _run(engine, _face(left=0.03), 1.0, 0.5)
    assert _fired(events) == [("left_wink", "left_click")]
    assert not any(e.steer for e in events)


def test_the_right_wink_is_unmapped_by_default():
    engine = _ready_engine()
    events, _ = _run(engine, _face(right=0.03), 1.0, 0.5)
    assert _fired(events) == [("right_wink", "none")]


def test_the_end_of_a_blink_is_not_a_wink():
    engine = _ready_engine()
    _, t = _run(engine, _face(left=0.03, right=0.03), 1.0, 0.15)
    events, _ = _run(engine, _face(left=0.03), t, 0.3)  # one eye reopens before the other
    assert _fired(events) == []


def test_closing_both_eyes_long_enough_pauses_once():
    engine = _ready_engine(long_blink_seconds=0.8)
    events, _ = _run(engine, _face(left=0.03, right=0.03), 1.0, 1.5)
    assert _fired(events) == [("long_blink", "pause")]


# ------------------------------------------------------------------ expressions
def test_raised_eyebrows_held_briefly_right_click_once():
    engine = _ready_engine()
    events, t = _run(engine, _face(brows=0.8), 1.0, 1.0)
    assert _fired(events) == [("raise_brows", "right_click")]
    assert not any(e.steer for e in events)  # the cursor waits for the click
    events, t = _run(engine, _face(brows=0.1), t, 0.2)  # relax...
    assert all(e.steer for e in events[1:])
    events, _ = _run(engine, _face(brows=0.8), t, 0.5)  # ...and it works again
    assert _fired(events) == [("raise_brows", "right_click")]


def test_a_flicker_of_an_expression_does_nothing():
    engine = _ready_engine()
    events, t = _run(engine, _face(pucker=0.9), 1.0, 0.1)
    events += _run(engine, _face(), t, 0.3)[0]
    assert _fired(events) == []


def test_puckered_lips_double_click():
    engine = _ready_engine()
    events, _ = _run(engine, _face(pucker=0.9), 1.0, 0.5)
    assert _fired(events) == [("pucker", "double_click")]


def test_a_smile_minimizes_the_window():
    engine = _ready_engine()
    events, _ = _run(engine, _face(smile=0.9), 1.0, 0.5)
    assert _fired(events) == [("smile", "minimize_window")]


def test_an_unmapped_expression_neither_fires_nor_holds_the_cursor():
    engine = _ready_engine(actions={"left_wink": "left_click"})
    events, _ = _run(engine, _face(smile=0.9), 1.0, 0.5)
    assert _fired(events) == [] and all(e.steer for e in events)


def test_thresholds_and_mappings_can_be_changed():
    engine = _ready_engine(actions={"smile": "left_click"}, thresholds={"smile": 0.3})
    events, _ = _run(engine, _face(smile=0.4), 1.0, 0.5)
    assert _fired(events) == [("smile", "left_click")]


def test_open_mouth_toggles_scroll_mode_and_looking_down_scrolls_down():
    engine = _ready_engine()
    events, t = _run(engine, _face(jaw=0.6), 1.0, 0.4)
    assert _fired(events) == [("mouth_open", "scroll_mode")] and engine.scroll_mode
    _, t = _run(engine, _face(), t, 0.2)
    still, t = _run(engine, _face(), t, 0.2)
    assert not any(e.scroll for e in still) and not any(e.steer for e in still)
    down, t = _run(engine, _face(nose=(0.5, 0.515)), t, 0.3)  # tilt beyond the dead zone
    assert sum(e.scroll for e in down) < 0
    up, t = _run(engine, _face(nose=(0.5, 0.485)), t, 0.3)
    assert sum(e.scroll for e in up) > 0
    events, t = _run(engine, _face(jaw=0.6), t, 0.4)
    assert not engine.scroll_mode
    assert _run(engine, _face(), t, 0.1)[0][-1].steer


def test_losing_the_face_does_nothing():
    engine = _ready_engine()
    assert engine.update(None, 2.0).name == "none"


# ------------------------------------------------------------------ dispatcher
def _dispatcher(mode="relative"):
    controller, executor, safety = _controller()
    return FaceDispatcher(controller, safety, smoothing=0.0, gain=3.0, pointer_mode=mode), controller, executor, safety


def _moves(executor):
    return [c for c in executor.calls if c[0] == "move"]


def test_relative_steering_moves_like_a_mouse_and_slow_is_finer():
    def travel(seconds):
        dispatcher, _, executor, _ = _dispatcher()
        engine = _ready_engine()
        frames = int(seconds / STEP)
        for i in range(frames + 1):
            x = 0.5 + 0.011 * i / frames  # the same head turn, done quickly or slowly
            t = 1.0 + i * STEP
            dispatcher.dispatch(engine.update(_face(nose=(x, 0.5)), t), t)
        moves = _moves(executor)
        return moves[-1][1] - moves[0][1]

    fast, slow = travel(0.1), travel(2.0)
    assert fast > 0 and slow > 0  # turning right moves the cursor right
    assert slow < fast / 2


def test_absolute_steering_points_where_the_head_faces():
    dispatcher, _, executor, _ = _dispatcher("absolute")
    engine = _ready_engine()
    dispatcher.neutral = engine.neutral
    dispatcher.dispatch(engine.update(_face(nose=(0.511, 0.5)), 2.0), 2.0)
    assert _moves(executor)[-1] == ("move", 650, 500)  # the test screen is 1000 x 1000


def test_expressions_click_through_the_controller():
    dispatcher, controller, executor, _ = _dispatcher()
    engine = _ready_engine()
    controller._clock = lambda: 0.0
    t = 1.0
    for face, expected in ((_face(left=0.03), ("click", "left")), (_face(brows=0.8), ("click", "right")),
                           (_face(pucker=0.9), ("double_click",)), (_face(smile=0.9), ("minimize",))):
        events, t = _run(engine, face, t, 0.5)
        for e in events:
            dispatcher.dispatch(e, t)
        assert expected in executor.calls
        controller._clock = lambda: 5.0  # far apart: no accidental double-click
        _, t = _run(engine, _face(), t, 0.3)


def test_scroll_mode_scrolls_the_page():
    dispatcher, _, executor, _ = _dispatcher()
    engine = _ready_engine()
    events, t = _run(engine, _face(jaw=0.6), 1.0, 0.4)
    events += _run(engine, _face(), t, 0.2)[0]
    events += _run(engine, _face(nose=(0.5, 0.52)), t + 0.2, 0.5)[0]
    labels = [r.label for r in (dispatcher.dispatch(e, 2.0) for e in events) if r]
    assert labels == ["Scroll Mode On"]
    assert any(c[0] == "scroll" and c[1] < 0 for c in executor.calls)


def test_paused_only_the_long_blink_works_and_it_resumes():
    dispatcher, _, executor, safety = _dispatcher()
    engine = _ready_engine()
    events, t = _run(engine, _face(left=0.03, right=0.03), 1.0, 1.0)
    for e in events:
        dispatcher.dispatch(e, t)
    assert safety.paused
    executor.calls.clear()
    _, t = _run(engine, _face(), t, 0.2)
    events, t = _run(engine, _face(left=0.03), t, 0.3)
    events += _run(engine, _face(nose=(0.52, 0.5)), t, 0.3)[0]
    for e in events:
        dispatcher.dispatch(e, t)
    assert executor.calls == []
    _, t = _run(engine, _face(), t + 0.3, 0.2)
    events, t = _run(engine, _face(left=0.03, right=0.03), t, 1.0)
    for e in events:
        dispatcher.dispatch(e, t)
    assert not safety.paused


def test_practice_mode_and_the_mouse_switch_block_face_input():
    dispatcher, controller, executor, _ = _dispatcher()
    engine = _ready_engine()
    controller.dry_run = True
    events, t = _run(engine, _face(left=0.03), 1.0, 0.3)
    events += _run(engine, _face(nose=(0.52, 0.5)), t, 0.3)[0]
    for e in events:
        dispatcher.dispatch(e, t)
    assert executor.calls == []
    controller.dry_run = False
    controller.enabled = dict(controller.enabled, mouse_control=False)
    for e in _run(engine, _face(nose=(0.48, 0.5)), t + 0.3, 0.3)[0]:
        dispatcher.dispatch(e, t + 0.6)
    assert executor.calls == []

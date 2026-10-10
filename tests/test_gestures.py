"""Tests for gesture classification and the stateful GestureEngine.

These build synthetic 21-point hand landmark arrays by hand rather than needing a real
camera/MediaPipe model, following the same geometry the production code reasons about
(tip farther from the wrist than the pip joint = extended finger).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.gesture_engine import (
    GestureEngine,
    INDEX_MCP, INDEX_PIP, INDEX_TIP,
    MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP,
    PINKY_MCP, PINKY_PIP, PINKY_TIP,
    RING_MCP, RING_PIP, RING_TIP,
    THUMB_MCP, THUMB_TIP,
    WRIST,
    classify_static_gesture,
)


def _hand(index=False, middle=False, ring=False, pinky=False, thumb=False, pinch=False) -> np.ndarray:
    pts = np.zeros((21, 3), dtype=np.float32)
    pts[WRIST] = [0.50, 0.90, 0.0]
    pts[THUMB_MCP] = [0.45, 0.80, 0.0]
    pts[THUMB_TIP] = [0.30, 0.75, 0.0] if thumb else [0.46, 0.79, 0.0]

    def place(mcp: int, pip: int, tip: int, x: float, extended: bool) -> None:
        pts[mcp] = [x, 0.65, 0.0]
        pts[pip] = [x, 0.55, 0.0]
        pts[tip] = [x, 0.25, 0.0] if extended else [x, 0.60, 0.0]

    place(INDEX_MCP, INDEX_PIP, INDEX_TIP, 0.52, index)
    place(MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP, 0.56, middle)
    place(RING_MCP, RING_PIP, RING_TIP, 0.60, ring)
    place(PINKY_MCP, PINKY_PIP, PINKY_TIP, 0.64, pinky)

    if pinch:
        pts[THUMB_TIP] = [0.52, 0.640, 0.0]
        pts[INDEX_TIP] = [0.52, 0.645, 0.0]
    return pts


def test_fist_is_all_fingers_curled():
    assert classify_static_gesture(_hand()) == "fist"


def test_open_palm_is_all_fingers_and_thumb_extended():
    assert classify_static_gesture(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True)) == "open_palm"


def test_pinch_overrides_other_shapes():
    assert classify_static_gesture(_hand(pinch=True)) == "pinch"


def test_two_fingers_is_index_and_middle_only():
    assert classify_static_gesture(_hand(index=True, middle=True)) == "two_fingers"


def test_point_is_index_only():
    assert classify_static_gesture(_hand(index=True)) == "point"


def test_ring_only_is_unknown():
    assert classify_static_gesture(_hand(ring=True)) == "unknown"


@dataclass
class _FakeHandResult:
    landmarks: np.ndarray
    score: float = 0.9


def test_discrete_gesture_fires_once_while_held_then_again_after_release():
    engine = GestureEngine(cooldown_seconds=0.5)
    pinch = _FakeHandResult(_hand(pinch=True))
    fist = _FakeHandResult(_hand())

    first = engine.update(pinch, now=0.0)
    second = engine.update(pinch, now=0.1)  # still held, same frame-ish
    assert first.name == "pinch" and first.is_trigger is True
    assert second.name == "pinch" and second.is_trigger is False  # no repeat click while held

    engine.update(fist, now=0.2)  # release
    third = engine.update(pinch, now=0.8)  # past the cooldown window
    assert third.name == "pinch" and third.is_trigger is True  # fires again on fresh pinch


def test_continuous_gesture_fires_every_frame():
    engine = GestureEngine()
    palm = _FakeHandResult(_hand(index=True, middle=True, ring=True, pinky=True, thumb=True))
    first = engine.update(palm, now=0.0)
    second = engine.update(palm, now=0.05)
    assert first.is_trigger is True
    assert second.is_trigger is True


def test_no_hand_returns_none_gesture():
    engine = GestureEngine()
    event = engine.update(None, now=0.0)
    assert event.name == "none"
    assert event.is_trigger is False

"""Gesture Engine: turns per-frame hand landmarks into classified, debounced gestures.

`classify_static_gesture` is a pure function (no state, no time) that looks at one
frame's 21 MediaPipe hand landmarks and names the hand shape. `GestureEngine` wraps it
with the stateful parts: swipe detection (which needs a short history of positions),
confidence gating, and per-gesture cooldowns so a held pinch fires one click, not one
click per frame.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import numpy as np

# MediaPipe hand landmark indices (see the Hand Landmarker docs for the full diagram).
WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

# Gestures whose action should re-fire every frame they're held (continuous control),
# vs. gestures that should fire once per transition into the pose (discrete actions).
CONTINUOUS_GESTURES = {"open_palm", "point"}
DISCRETE_GESTURES = {"pinch", "two_fingers", "fist", "swipe_left", "swipe_right"}


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    """2D distance (x, y only - a single webcam's z estimate is too noisy to rely on)."""
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _finger_extended(landmarks: np.ndarray, pip: int, tip: int) -> bool:
    wrist = landmarks[WRIST]
    return _dist(landmarks[tip], wrist) > _dist(landmarks[pip], wrist) * 1.05


def classify_static_gesture(landmarks: np.ndarray, pinch_threshold: float = 0.4) -> str:
    """Classifies one frame's hand shape: pinch, fist, open_palm, two_fingers, point, or unknown.

    `pinch_threshold` is the thumb-to-index gap (normalized by palm size) below which the
    hand counts as pinching.
    """
    wrist = landmarks[WRIST]
    scale = max(_dist(landmarks[MIDDLE_MCP], wrist), 1e-4)  # palm-size reference

    pinch_dist = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / scale
    if pinch_dist < pinch_threshold:
        return "pinch"

    thumb_extended = _dist(landmarks[THUMB_TIP], landmarks[PINKY_MCP]) > (
        _dist(landmarks[THUMB_MCP], landmarks[PINKY_MCP]) * 1.05
    )
    index = _finger_extended(landmarks, INDEX_PIP, INDEX_TIP)
    middle = _finger_extended(landmarks, MIDDLE_PIP, MIDDLE_TIP)
    ring = _finger_extended(landmarks, RING_PIP, RING_TIP)
    pinky = _finger_extended(landmarks, PINKY_PIP, PINKY_TIP)
    extended_count = sum([index, middle, ring, pinky])

    if extended_count == 0 and not thumb_extended:
        return "fist"
    if extended_count == 4 and thumb_extended:
        return "open_palm"
    if index and middle and not ring and not pinky:
        return "two_fingers"
    if index and not middle and not ring and not pinky:
        return "point"
    return "unknown"


@dataclass
class GestureEvent:
    name: str
    confidence: float  # 0-100
    index_tip: tuple[float, float] | None  # normalized (x, y), for cursor mapping
    motion_delta: tuple[float, float] | None  # frame-to-frame index-tip movement
    is_trigger: bool  # True on the frame this gesture's action should fire


class GestureEngine:
    def __init__(
        self,
        pinch_threshold: float = 0.4,
        swipe_min_distance: float = 0.15,
        swipe_max_duration: float = 0.6,
        cooldown_seconds: float = 0.6,
    ) -> None:
        self.pinch_threshold = pinch_threshold
        self.swipe_min_distance = swipe_min_distance
        self.swipe_max_duration = swipe_max_duration
        self.cooldown_seconds = cooldown_seconds

        self._position_history: deque[tuple[float, float, float]] = deque(maxlen=30)
        self._prev_raw_gesture = "none"
        self._prev_index_tip: tuple[float, float] | None = None
        self._last_fire_time: dict[str, float] = {}
        self._swipe_cooldown_until = 0.0

    def reset(self) -> None:
        self._position_history.clear()
        self._prev_raw_gesture = "none"
        self._prev_index_tip = None

    def update(self, hand_result, now: float | None = None) -> GestureEvent:
        now = time.monotonic() if now is None else now

        if hand_result is None:
            self.reset()
            return GestureEvent("none", 0.0, None, None, False)

        landmarks = hand_result.landmarks
        index_tip = (float(landmarks[INDEX_TIP][0]), float(landmarks[INDEX_TIP][1]))
        motion_delta = None
        if self._prev_index_tip is not None:
            motion_delta = (index_tip[0] - self._prev_index_tip[0], index_tip[1] - self._prev_index_tip[1])
        self._prev_index_tip = index_tip

        static_gesture = classify_static_gesture(landmarks, self.pinch_threshold)
        self._position_history.append((now, index_tip[0], index_tip[1]))

        swipe = None
        if static_gesture in ("open_palm", "point", "unknown") and now >= self._swipe_cooldown_until:
            swipe = self._detect_swipe(now)

        gesture = swipe or static_gesture
        confidence = max(0.0, min(100.0, hand_result.score * 100.0))

        is_trigger = False
        if gesture in CONTINUOUS_GESTURES:
            is_trigger = True
        elif gesture in DISCRETE_GESTURES:
            last_fire = self._last_fire_time.get(gesture)
            if gesture != self._prev_raw_gesture and (last_fire is None or (now - last_fire) >= self.cooldown_seconds):
                is_trigger = True
                self._last_fire_time[gesture] = now
            if gesture.startswith("swipe"):
                self._swipe_cooldown_until = now + self.cooldown_seconds
                self._position_history.clear()

        self._prev_raw_gesture = gesture
        return GestureEvent(gesture, confidence, index_tip, motion_delta, is_trigger)

    def _detect_swipe(self, now: float) -> str | None:
        if len(self._position_history) < 4:
            return None
        window = [p for p in self._position_history if now - p[0] <= self.swipe_max_duration]
        if len(window) < 4:
            return None
        start, end = window[0], window[-1]
        dx = end[1] - start[1]
        dy = end[2] - start[2]
        if abs(dx) < self.swipe_min_distance or abs(dx) <= abs(dy) * 1.5:
            return None
        return "swipe_right" if dx > 0 else "swipe_left"

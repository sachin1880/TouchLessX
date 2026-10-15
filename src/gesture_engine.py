"""Gesture Engine: turns per-frame hand landmarks into classified, debounced gestures.

`classify_static_gesture` is a pure function (no state, no time) that looks at one
frame's 21 MediaPipe hand landmarks and names the hand shape. `GestureEngine` wraps it
with the stateful parts: swipe detection (which needs a short history of positions),
confidence gating, and per-gesture cooldowns so a held pinch fires one click, not one
click per frame.
"""
from __future__ import annotations

import time
from collections import Counter, deque
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
CONTINUOUS_GESTURES = {"open_palm", "point", "three_fingers"}
DISCRETE_GESTURES = {
    "pinch", "two_fingers", "fist", "thumbs_up", "swipe_left", "swipe_right", "swipe_up", "swipe_down",
    "hand_open", "point_left", "point_right",
}
BUILTIN_GESTURES = (
    "open_palm", "point", "three_fingers", "pinch", "two_fingers", "fist", "swipe_left", "swipe_right",
    "thumbs_up", "swipe_up", "swipe_down", "hand_open", "point_left", "point_right",
    "both_palms", "two_hand_pinch", "thumb_index",
)
# Gestures made with both hands raised. While two raised hands are in view and one of
# these is in use, only they count - single-hand gestures are suspended, so the second
# hand joining in can't click or scroll by accident.
TWO_HAND_GESTURES = ("both_palms", "two_hand_pinch")
TWO_HAND_HOLD_SECONDS = 0.6  # both palms must be shown at least this long before they act
# Hand shapes that can drive either kind of action - a continuous one (cursor, scroll,
# volume) while held, or a one-shot one - depending on what they are mapped to.
FLEXIBLE_POSES = ("open_palm", "point", "two_fingers", "three_fingers", "fist", "thumbs_up",
                  "point_left", "point_right", "both_palms", "two_hand_pinch", "thumb_index")
# Gestures that are a movement rather than a pose. They are reported for a single
# frame in place of the pose underneath, so they are only looked for while in use
# (see `GestureEngine.muted`).
MOTION_GESTURES = ("swipe_left", "swipe_right", "swipe_up", "swipe_down", "hand_open")
# Gestures that are only looked for while mapped to something. An unused sideways point
# simply counts as "point", so tilting the finger doesn't drop the cursor.
OPTIONAL_GESTURES = MOTION_GESTURES + ("point_left", "point_right", "thumb_index") + TWO_HAND_GESTURES
# "hand_open" = the hand opening out of a fist within this many seconds.
HAND_OPEN_WINDOW_SECONDS = 1.2
# User-trained poses (see custom_gestures.py) are reported as "custom:<name>" and
# always behave as discrete gestures.
CUSTOM_PREFIX = "custom:"
# With the other fingers curled, the index fingertip must be at least this far from the
# wrist (relative to its middle joint) for a thumb-index touch to be a pinch, not a fist.
PINCH_MIN_INDEX_REACH = 0.9
# The thumb tip must be this far (in palm lengths) above every other part of the hand.
THUMBS_UP_CLEARANCE = 0.15
# A pinch releases only once the gap exceeds the pinch threshold by this factor.
PINCH_RELEASE_RATIO = 1.25
# Pinches may repeat this quickly regardless of the general cooldown, so two quick
# pinches can form a double-click.
PINCH_REPEAT_SECONDS = 0.15
# "thumb_index" (an L: index and thumb out, the rest curled) is a one-hand zoom: its
# movement is the change in the thumb-index gap, scaled to the two-hand zoom's steps.
THUMB_OUT_MIN_GAP = 0.6       # thumb-index gap (palm lengths) for the thumb to count as out
THUMB_INDEX_ZOOM_SCALE = 0.35


def is_continuous(gesture: str) -> bool:
    return gesture in CONTINUOUS_GESTURES


def is_discrete(gesture: str) -> bool:
    return gesture in DISCRETE_GESTURES or gesture.startswith(CUSTOM_PREFIX)


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    """2D distance (x, y only - a single webcam's z estimate is too noisy to rely on)."""
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def pinch_distance(landmarks: np.ndarray) -> float:
    """Gap between thumb and index fingertips, in palm lengths."""
    scale = max(_dist(landmarks[MIDDLE_MCP], landmarks[WRIST]), 1e-4)
    return _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / scale


def _thumb_out(landmarks: np.ndarray) -> bool:
    """The thumb held out from the hand, clear of the index finger - the L of "thumb_index"."""
    thumb_extended = _dist(landmarks[THUMB_TIP], landmarks[PINKY_MCP]) > (
        _dist(landmarks[THUMB_MCP], landmarks[PINKY_MCP]) * 1.05)
    return thumb_extended and pinch_distance(landmarks) >= THUMB_OUT_MIN_GAP


def _finger_extended(landmarks: np.ndarray, pip: int, tip: int) -> bool:
    wrist = landmarks[WRIST]
    return _dist(landmarks[tip], wrist) > _dist(landmarks[pip], wrist) * 1.05


def classify_static_gesture(landmarks: np.ndarray, pinch_threshold: float = 0.4) -> str:
    """Classifies one frame's hand shape: pinch, fist, open_palm, two_fingers, three_fingers,
    point, or unknown.

    `pinch_threshold` is the thumb-to-index gap (normalized by palm size) below which the
    hand counts as pinching.
    """
    wrist = landmarks[WRIST]
    scale = max(_dist(landmarks[MIDDLE_MCP], wrist), 1e-4)  # palm-size reference

    thumb_extended = _dist(landmarks[THUMB_TIP], landmarks[PINKY_MCP]) > (
        _dist(landmarks[THUMB_MCP], landmarks[PINKY_MCP]) * 1.05
    )
    index = _finger_extended(landmarks, INDEX_PIP, INDEX_TIP)
    middle = _finger_extended(landmarks, MIDDLE_PIP, MIDDLE_TIP)
    ring = _finger_extended(landmarks, RING_PIP, RING_TIP)
    pinky = _finger_extended(landmarks, PINKY_PIP, PINKY_TIP)
    extended_count = sum([index, middle, ring, pinky])

    # A pinch is thumb and index fingertips together - but in a fist (or any relaxed,
    # half-closed hand) the thumb also rests against the curled index finger. So the
    # fingertips being close only counts when the hand is otherwise open, or when the
    # index finger is at least reaching forward rather than curled into the palm.
    if pinch_distance(landmarks) < pinch_threshold:
        index_reach = _dist(landmarks[INDEX_TIP], wrist) / max(_dist(landmarks[INDEX_PIP], wrist), 1e-4)
        if sum([middle, ring, pinky]) >= 2 or index_reach >= PINCH_MIN_INDEX_REACH:
            return "pinch"

    if extended_count == 0:
        # All four fingers curled. A thumbs-up only if the thumb stands clearly above the
        # whole rest of the hand; otherwise it is a fist, wherever the thumb happens to
        # rest. (An upright fist with the thumb alongside has its knuckles on top.)
        thumb_y = landmarks[THUMB_TIP][1]
        others_top = float(np.min(np.delete(landmarks[:, 1], [THUMB_TIP, THUMB_TIP - 1])))
        if (thumb_extended and thumb_y < landmarks[THUMB_MCP][1] - 0.5 * scale
                and thumb_y < others_top - THUMBS_UP_CLEARANCE * scale):
            return "thumbs_up"
        return "fist"
    if extended_count == 4 and thumb_extended:
        return "open_palm"
    if index and middle and ring and not pinky:
        return "three_fingers"
    if index and middle and not ring and not pinky:
        return "two_fingers"
    if index and not middle and not ring and not pinky:
        dx, dy = (landmarks[INDEX_TIP] - landmarks[INDEX_MCP])[:2]
        if abs(dx) > abs(dy) * 1.7:  # finger clearly lying sideways (60 degrees or more off vertical)
            return "point_left" if dx < 0 else "point_right"
        return "point"
    return "unknown"


@dataclass
class GestureEvent:
    name: str
    confidence: float  # 0-100
    index_tip: tuple[float, float] | None  # normalized (x, y), for cursor mapping
    motion_delta: tuple[float, float] | None  # frame-to-frame hand (palm) movement
    is_trigger: bool  # True on the frame this gesture's action should fire
    hold_time: float = 0.0  # seconds this gesture has been held continuously
    palm: tuple[float, float] | None = None  # normalized palm centre - the steadiest point to steer with


class GestureEngine:
    def __init__(
        self,
        pinch_threshold: float = 0.4,
        swipe_min_distance: float = 0.15,
        swipe_max_duration: float = 0.6,
        cooldown_seconds: float = 0.6,
        custom_classifier=None,
        stable_frames: int = 1,
        pose_hold_seconds: float = 0.0,
    ) -> None:
        self.stable_frames = stable_frames
        self.pose_hold_seconds = pose_hold_seconds
        # Optional object with `predict(landmarks) -> str | None`, consulted only for
        # poses the built-in rules don't recognise, and only while `custom_enabled`.
        self.custom_classifier = custom_classifier
        self.custom_enabled = True
        self.pinch_threshold = pinch_threshold
        self.swipe_min_distance = swipe_min_distance
        self.swipe_max_duration = swipe_max_duration
        self.cooldown_seconds = cooldown_seconds

        self._position_history: deque[tuple[float, float, float]] = deque(maxlen=30)
        self._prev_gesture = "none"
        self._gesture_since = 0.0
        self._evaluated = False  # has the current hold of a one-shot gesture been judged yet?
        self._candidate: str | None = None
        self._candidate_frames = 0
        self._stable_static = "unknown"
        self._prev_palm: tuple[float, float] | None = None
        self._prev_spread: float | None = None  # distance between the two palms, last frame
        self._prev_gap: float | None = None     # thumb-index gap of a "thumb_index" pose, last frame
        self._last_fire_time: dict[str, float] = {}
        self._swipe_cooldown_until = 0.0
        # One-shot poses that were let go before `pose_hold_seconds` - surfaced in the
        # insights page, since many of these mean the hold time is set too long.
        self.brief_poses: Counter = Counter()
        # Motion gestures not to report - the app mutes the ones mapped to "No action",
        # so e.g. an unused swipe can't interrupt a scroll that is in progress.
        self.muted: set[str] = set()
        # Which poses currently drive a continuous action (set by the app from the
        # mappings), and optional per-gesture cooldowns overriding `cooldown_seconds`.
        self.continuous_gestures: set[str] = set(CONTINUOUS_GESTURES)
        self.cooldowns: dict[str, float] = {}
        self.last_raw = "none"  # this frame's hand shape before debouncing, for diagnostics
        # A hand whose wrist is at or below this height (0 = top of the frame, 1 = bottom)
        # is resting at the edge of the picture - on the desk or keyboard, half out of
        # view - and is ignored. None = no such rule. Recorded use showed such a hand is
        # mostly seen as a "fist", which is how windows got minimised by accident.
        self.rest_edge: float | None = None
        self._fist_seen_at = -1e9

    def reset(self) -> None:
        self._position_history.clear()
        self._prev_gesture = "none"
        self._evaluated = False
        self._candidate = None
        self._candidate_frames = 0
        self._stable_static = "unknown"
        self._prev_palm = None
        self._prev_spread = None
        self._prev_gap = None

    def _stable_pose(self, landmarks: np.ndarray) -> str:
        """The current hand shape, debounced: a new shape only counts once it has been
        seen for `stable_frames` frames in a row. Opening or closing the hand passes
        through other shapes for a frame or two - those must not register."""
        # Hysteresis: once pinching, the fingers must open clearly further to un-pinch,
        # so a pinch hovering at the threshold doesn't flicker into repeated clicks.
        threshold = self.pinch_threshold * (PINCH_RELEASE_RATIO if self._stable_static == "pinch" else 1.0)
        raw = classify_static_gesture(landmarks, threshold)
        if raw in ("point_left", "point_right") and raw in self.muted:
            raw = "point"
        if "thumb_index" not in self.muted:
            if raw in ("point", "point_left", "point_right") and _thumb_out(landmarks):
                raw = "thumb_index"
            elif raw == "pinch" and self._stable_static == "thumb_index":
                raw = "thumb_index"  # closing the L to zoom out must never click
        self.last_raw = raw
        if raw == "unknown" and self.custom_classifier is not None and self.custom_enabled:
            name = self.custom_classifier.predict(landmarks)
            if name:
                raw = CUSTOM_PREFIX + name
        if raw == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate, self._candidate_frames = raw, 1
        if self._candidate_frames >= max(1, int(self.stable_frames)):
            self._stable_static = raw
        return self._stable_static

    def update(self, hand_result, now: float | None = None) -> GestureEvent:
        now = time.monotonic() if now is None else now

        if hand_result is None:
            self.reset()
            return GestureEvent("none", 0.0, None, None, False)

        landmarks = hand_result.landmarks
        if self.rest_edge is not None and float(landmarks[WRIST][1]) > self.rest_edge:
            self.reset()
            self._fist_seen_at = -1e9  # a fist that then went to rest was not "opened"
            self.last_raw = "resting"
            return GestureEvent("resting", max(0.0, min(100.0, hand_result.score * 100.0)), None, None, False)
        other = getattr(hand_result, "other", None)
        if (
            other is not None and not set(TWO_HAND_GESTURES) <= self.muted
            and (self.rest_edge is None or float(other.landmarks[WRIST][1]) <= self.rest_edge)
        ):
            return self._update_two_hands(hand_result, other, now)
        self._prev_spread = None
        index_tip = (float(landmarks[INDEX_TIP][0]), float(landmarks[INDEX_TIP][1]))
        # Movement is measured at the palm, not a fingertip: the palm stays put while
        # the fingers pinch or curl, so clicking doesn't nudge the cursor.
        palm_xy = landmarks[[WRIST, INDEX_MCP, PINKY_MCP], :2].mean(axis=0)
        palm = (float(palm_xy[0]), float(palm_xy[1]))
        motion_delta = None
        if self._prev_palm is not None:
            motion_delta = (palm[0] - self._prev_palm[0], palm[1] - self._prev_palm[1])
        self._prev_palm = palm

        static_gesture = self._stable_pose(landmarks)
        if static_gesture == "thumb_index":
            # The one-hand zoom moves by the thumb-index gap, not by the palm.
            gap = pinch_distance(landmarks)
            motion_delta = None if self._prev_gap is None else (
                (gap - self._prev_gap) * THUMB_INDEX_ZOOM_SCALE, 0.0)
            self._prev_gap = gap
        else:
            self._prev_gap = None
        self._position_history.append((now, palm[0], palm[1]))

        swipe = None
        if static_gesture in ("open_palm", "unknown") and now >= self._swipe_cooldown_until:
            swipe = self._detect_swipe(now)
            if swipe in self.muted:
                swipe = None

        gesture = swipe or static_gesture
        if (
            gesture == "open_palm"
            and self._prev_gesture != "open_palm"
            and now - self._fist_seen_at <= HAND_OPEN_WINDOW_SECONDS
            and "hand_open" not in self.muted
        ):
            gesture = "hand_open"
            self._fist_seen_at = -1e9
        confidence = max(0.0, min(100.0, hand_result.score * 100.0))
        if gesture != self._prev_gesture:
            if is_discrete(self._prev_gesture) and not self._evaluated:
                self.brief_poses[self._prev_gesture] += 1
            self._gesture_since = now
            self._evaluated = False
        hold_time = now - self._gesture_since

        is_trigger = False
        if gesture in self.continuous_gestures:
            is_trigger = True
        elif is_discrete(gesture) and not self._evaluated:
            # Pinch (the click) and swipes act at once; other one-shot poses must be
            # held briefly, so a hand that merely passes through "fist" does nothing.
            instant = gesture == "pinch" or gesture in MOTION_GESTURES
            if instant or hold_time >= self.pose_hold_seconds:
                self._evaluated = True
                cooldown = self.cooldowns.get(gesture, self.cooldown_seconds)
                if gesture == "pinch":
                    cooldown = min(cooldown, PINCH_REPEAT_SECONDS)
                last_fire = self._last_fire_time.get(gesture)
                if last_fire is None or (now - last_fire) >= cooldown:
                    is_trigger = True
                    self._last_fire_time[gesture] = now
                if gesture.startswith("swipe"):
                    self._swipe_cooldown_until = now + cooldown
                    self._position_history.clear()

        if gesture == "fist" and self._evaluated:
            self._fist_seen_at = now  # a deliberate fist (held long enough to count)
        self._prev_gesture = gesture
        return GestureEvent(gesture, confidence, index_tip, motion_delta, is_trigger, hold_time, palm)

    def _update_two_hands(self, first, second, now: float) -> GestureEvent:
        """Both hands are raised: look for a two-hand gesture and nothing else."""
        shapes = {classify_static_gesture(hand.landmarks, self.pinch_threshold) for hand in (first, second)}
        raw = {"open_palm": "both_palms", "pinch": "two_hand_pinch"}.get(next(iter(shapes)), "two_hands") \
            if len(shapes) == 1 else "two_hands"
        if raw in self.muted:
            raw = "two_hands"
        self.last_raw = raw
        if raw == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate, self._candidate_frames = raw, 1
        if self._candidate_frames >= max(1, int(self.stable_frames)):
            self._stable_static = raw
        gesture = self._stable_static if self._stable_static in TWO_HAND_GESTURES else "two_hands"

        palms = [hand.landmarks[[WRIST, INDEX_MCP, PINKY_MCP], :2].mean(axis=0) for hand in (first, second)]
        spread = float(np.hypot(*(palms[0] - palms[1])))
        # For a two-hand gesture "movement" is the hands moving apart (+) or together (-).
        motion_delta = (spread - self._prev_spread, 0.0) if self._prev_spread is not None else None
        self._prev_spread = spread
        self._prev_palm = None            # single-hand movement starts afresh afterwards
        self._position_history.clear()    # ...and this was not a swipe

        if gesture != self._prev_gesture:
            if is_discrete(self._prev_gesture) and not self._evaluated:
                self.brief_poses[self._prev_gesture] += 1
            self._gesture_since = now
            self._evaluated = False
        hold_time = now - self._gesture_since

        is_trigger = False
        if gesture in self.continuous_gestures:
            is_trigger = True
        elif gesture in TWO_HAND_GESTURES and not self._evaluated:
            if hold_time >= max(self.pose_hold_seconds, TWO_HAND_HOLD_SECONDS):
                self._evaluated = True
                last_fire = self._last_fire_time.get(gesture)
                if last_fire is None or now - last_fire >= self.cooldowns.get(gesture, self.cooldown_seconds):
                    is_trigger = True
                    self._last_fire_time[gesture] = now
        self._prev_gesture = gesture
        confidence = max(0.0, min(100.0, min(first.score, second.score) * 100.0))
        return GestureEvent(gesture, confidence, None, motion_delta, is_trigger, hold_time, None)

    def _detect_swipe(self, now: float) -> str | None:
        if len(self._position_history) < 4:
            return None
        window = [p for p in self._position_history if now - p[0] <= self.swipe_max_duration]
        if len(window) < 4:
            return None
        start, end = window[0], window[-1]
        dx = end[1] - start[1]
        dy = end[2] - start[2]
        if abs(dx) >= self.swipe_min_distance and abs(dx) > abs(dy) * 1.5:
            return "swipe_right" if dx > 0 else "swipe_left"
        if abs(dy) >= self.swipe_min_distance and abs(dy) > abs(dx) * 1.5:
            return "swipe_down" if dy > 0 else "swipe_up"
        return None

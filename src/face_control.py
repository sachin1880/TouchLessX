"""Face Control: a hands-free mode. Turning the head steers the cursor and facial
expressions - a wink, raised eyebrows, puckered lips, an open mouth, a smile - are
mapped to clicks, scroll mode and pause. It replaces hand gestures while it is switched
on, so the two never fight over the cursor.

The tracker wraps MediaPipe's Face Landmarker; `FaceEngine` turns its landmarks and
expression scores into events and holds all the decisions (kept free of MediaPipe so it
can be tested), and `FaceDispatcher` carries the events out through the shared
`ActionController`, so practice mode, pause and the feature switches apply exactly as
they do for hands."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .action_controller import DispatchResult, _OneEuroFilter
from .hand_tracker import TrackerError, ensure_model

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

# Face-mesh landmark indices (MediaPipe's 478-point layout).
NOSE_TIP = 1
LIP_UPPER, LIP_LOWER = 13, 14
# Per eye: the two corners, then the upper and lower lid points across the middle.
EYE_A = (33, 133, (160, 159, 158), (144, 145, 153))
EYE_B = (362, 263, (385, 386, 387), (380, 374, 373))

# Expressions that can be mapped to an action: (label, MediaPipe blendshapes scored).
# Winks are measured from the eyelids instead (see `FaceEngine._eye_events`): the
# blendshape scores don't reliably tell one eye from the other on a mirrored frame.
EXPRESSIONS = {
    "left_wink": ("Wink left eye", ()),
    "right_wink": ("Wink right eye", ()),
    "raise_brows": ("Raise eyebrows", ("browInnerUp",)),
    "pucker": ("Pucker lips (kiss)", ("mouthPucker",)),
    "mouth_open": ("Open mouth", ("jawOpen",)),
    "smile": ("Smile", ("mouthSmileLeft", "mouthSmileRight")),
}
FACE_ACTIONS = {
    "none": "No action",
    "left_click": "Click",
    "double_click": "Double-click (open)",
    "right_click": "Right click",
    "minimize_window": "Minimize window",
    "scroll_mode": "Scroll mode on / off",
    "pause": "Pause / resume",
}
POINTER_MODES = {"relative": "Like a mouse (precise)", "absolute": "Point with your head"}

CALIBRATION_SECONDS = 0.6   # the head position averaged as "looking at the centre"
WINK_MIN_SECONDS = 0.12     # one eye shut at least this long - a stray frame is not a wink
EYE_OPEN_MARGIN = 0.2       # hysteresis: an eye counts as open again only well above the threshold
FREEZE_MARGIN = 0.15        # the cursor stops once an eye starts closing, so a wink clicks in place
EXPRESSION_HOLD = 0.25      # an expression must be held this long to act
EXPRESSION_RELEASE = 0.7    # ...and drop below this share of its threshold before it can act again
EXPRESSION_BUILDING = 0.6   # from this share of its threshold an expression freezes the cursor
SCROLL_DEAD_ZONE = 0.04     # head tilt (per eye distance) that scrolls nothing in scroll mode
SCROLL_GAIN = 6000.0        # pixels per second per unit of tilt beyond the dead zone


class FaceError(TrackerError):
    """Raised when the face model can't be fetched or loaded."""


@dataclass
class FaceResult:
    landmarks: np.ndarray                       # shape (478, 3): normalized (x, y, z)
    blendshapes: dict = field(default_factory=dict)  # expression name -> score 0..1


@dataclass
class FaceEvent:
    name: str                                   # "none", "face", an expression name, or "long_blink"
    trigger: bool = False                       # the frame an expression fires
    action: str = "none"                        # what the fired expression is mapped to
    head: tuple[float, float] | None = None     # head direction (see `head_direction`)
    steer: bool = False                         # the cursor may follow the head this frame
    scroll: float = 0.0                         # pixels this frame, + = up
    scroll_mode: bool = False
    eyes: tuple[float, float] = (1.0, 1.0)      # (user's left, user's right) openness, 1.0 = their normal
    scores: dict = field(default_factory=dict)  # expression -> score 0..1
    calibrating: bool = False


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


def eye_openness(landmarks: np.ndarray, eye: tuple) -> float:
    """Lid gap divided by eye width: about 0.25-0.35 open, under 0.1 shut."""
    corner_a, corner_b, upper, lower = eye
    width = _dist(landmarks[corner_a], landmarks[corner_b])
    gap = sum(_dist(landmarks[u], landmarks[d]) for u, d in zip(upper, lower)) / len(upper)
    return gap / width if width > 1e-6 else 0.0


def head_direction(landmarks: np.ndarray) -> tuple[float, float]:
    """Where the head points: the nose tip's offset from the point between the eyes, per
    eye distance. Turning or nodding moves it; leaning the body or opening the mouth
    barely does, because the eyes and nose move together and the jaw isn't involved."""
    eye_a = (landmarks[EYE_A[0]][:2] + landmarks[EYE_A[1]][:2]) / 2.0
    eye_b = (landmarks[EYE_B[0]][:2] + landmarks[EYE_B[1]][:2]) / 2.0
    span = _dist(eye_a, eye_b)
    if span < 1e-6:
        return 0.0, 0.0
    middle = (eye_a + eye_b) / 2.0
    nose = landmarks[NOSE_TIP]
    return float((nose[0] - middle[0]) / span), float((nose[1] - middle[1]) / span)


@dataclass
class _Eyes:
    baseline: list = field(default_factory=lambda: [None, None])  # each eye's normal open value
    closed: list = field(default_factory=lambda: [False, False])
    both_since: float | None = None
    one_since: float | None = None
    one_side: int | None = None
    fired: bool = False  # this closure already produced its event


class FaceEngine:
    def __init__(self, wink_threshold: float = 0.55, long_blink_seconds: float = 0.8,
                 scroll_speed: float = 1.0, actions: dict | None = None, thresholds: dict | None = None) -> None:
        self.wink_threshold = wink_threshold     # shut = below this share of the eye's normal opening
        self.long_blink_seconds = long_blink_seconds
        self.scroll_speed = scroll_speed
        self.actions = dict(actions or {"left_wink": "left_click", "raise_brows": "right_click",
                                        "pucker": "double_click", "mouth_open": "scroll_mode",
                                        "smile": "minimize_window"})
        self.thresholds = dict(thresholds or {"raise_brows": 0.5, "pucker": 0.6, "mouth_open": 0.35, "smile": 0.6})
        self.reset()

    def reset(self) -> None:
        self._eyes = _Eyes()
        self._held: dict[str, float] = {}    # expression -> since when it has been held
        self._spent: set[str] = set()        # expressions that fired and haven't been released
        self.scroll_mode = False
        self._scroll_anchor = 0.0
        self._last_time: float | None = None
        self.recenter()

    def recenter(self) -> None:
        """The next moments of head position become the centre."""
        self.neutral: tuple[float, float] | None = None
        self._samples: list[tuple[float, float]] = []
        self._calibration_start: float | None = None

    def update(self, result: FaceResult | None, now: float) -> FaceEvent:
        dt = 0.0 if self._last_time is None else min(0.1, max(0.0, now - self._last_time))
        self._last_time = now
        if result is None:
            self._eyes.both_since = self._eyes.one_since = None
            self._eyes.fired = False
            self._held.clear()
            return FaceEvent("none", scroll_mode=self.scroll_mode)
        lm = result.landmarks
        head = head_direction(lm)

        if self.neutral is None:
            if self._calibration_start is None:
                self._calibration_start = now
            self._samples.append(head)
            if now - self._calibration_start < CALIBRATION_SECONDS:
                return FaceEvent("face", head=head, calibrating=True, scroll_mode=self.scroll_mode)
            self.neutral = tuple(np.mean(self._samples, axis=0))

        eyes = self._eye_levels(lm)
        scores = {name: max((result.blendshapes.get(b, 0.0) for b in shapes), default=0.0)
                  for name, (_, shapes) in EXPRESSIONS.items() if shapes}
        event = FaceEvent("face", head=head, eyes=eyes, scores=scores, scroll_mode=self.scroll_mode)

        fired = self._eye_events(eyes, now)
        if fired == "long_blink":
            event.name, event.trigger, event.action = "long_blink", True, "pause"
        elif fired:
            event.name, event.trigger, event.action = fired, True, self.actions.get(fired, "none")
        building = self._expressions(scores, now, event)

        if event.trigger and event.action == "scroll_mode":
            self.scroll_mode = not self.scroll_mode
            self._scroll_anchor = head[1]
            event.scroll_mode = self.scroll_mode
        elif event.trigger and event.action == "pause":
            self.scroll_mode = event.scroll_mode = False

        if self.scroll_mode:
            tilt = head[1] - self._scroll_anchor
            if abs(tilt) > SCROLL_DEAD_ZONE:
                beyond = abs(tilt) - SCROLL_DEAD_ZONE
                # Looking down (nose lower, + tilt) scrolls down (- pixels).
                event.scroll = -math.copysign(beyond, tilt) * SCROLL_GAIN * self.scroll_speed * dt
        closing = min(eyes) < self.wink_threshold + FREEZE_MARGIN
        event.steer = not (self.scroll_mode or closing or building or event.trigger)
        return event

    def _expressions(self, scores: dict, now: float, event: FaceEvent) -> bool:
        """Fires a mapped expression held long enough. True while one is building up, so
        the cursor holds still and the click lands where it was aimed."""
        building = False
        for name, score in scores.items():
            action = self.actions.get(name, "none")
            threshold = self.thresholds.get(name, 0.6)
            if score < threshold * EXPRESSION_RELEASE:
                self._held.pop(name, None)
                self._spent.discard(name)
                continue
            if action != "none" and score >= threshold * EXPRESSION_BUILDING:
                building = True  # also after it fired: the cursor waits until the face relaxes
            if score < threshold:
                self._held.pop(name, None)
                continue
            since = self._held.setdefault(name, now)
            if action == "none" or name in self._spent or event.trigger:
                continue
            if now - since >= EXPRESSION_HOLD:
                self._spent.add(name)
                event.name, event.trigger, event.action = name, True, action
        return building

    def _eye_levels(self, lm: np.ndarray) -> tuple[float, float]:
        """Each eye's opening relative to its own normal, user's left eye first. Frames
        arrive mirrored, so the user's left eye is the one further left in the image."""
        a, b = eye_openness(lm, EYE_A), eye_openness(lm, EYE_B)
        a_x = (lm[EYE_A[0]][0] + lm[EYE_A[1]][0]) / 2.0
        b_x = (lm[EYE_B[0]][0] + lm[EYE_B[1]][0]) / 2.0
        raw = (a, b) if a_x < b_x else (b, a)
        levels = []
        for i, value in enumerate(raw):
            base = self._eyes.baseline[i]
            if base is None:
                base = self._eyes.baseline[i] = value
            elif value > base * 0.75:
                # Follow the eye's normal opening slowly, only while it is open.
                base = self._eyes.baseline[i] = 0.97 * base + 0.03 * value
            levels.append(value / base if base > 1e-6 else 1.0)
        return levels[0], levels[1]

    def _eye_events(self, levels: tuple[float, float], now: float) -> str | None:
        eyes = self._eyes
        for i, level in enumerate(levels):
            if level < self.wink_threshold:
                eyes.closed[i] = True
            elif level > self.wink_threshold + EYE_OPEN_MARGIN:
                eyes.closed[i] = False
        both, one = all(eyes.closed), any(eyes.closed) and not all(eyes.closed)
        if not both and not one:
            eyes.both_since = eyes.one_since = eyes.one_side = None
            eyes.fired = False
            return None
        if both:
            eyes.one_since = None
            if eyes.both_since is None:
                eyes.both_since = now
            if not eyes.fired and now - eyes.both_since >= self.long_blink_seconds:
                eyes.fired = True
                return "long_blink"
            return None
        # One eye shut. An ordinary blink passes through this for a frame or two on its
        # way to both shut, so it only counts once it has lasted a while on its own.
        side = 0 if eyes.closed[0] else 1
        if eyes.both_since is not None:
            return None  # the tail of a blink: one eye reopened first
        if eyes.one_since is None or eyes.one_side != side:
            eyes.one_since, eyes.one_side = now, side
        if not eyes.fired and now - eyes.one_since >= WINK_MIN_SECONDS:
            eyes.fired = True
            return "left_wink" if side == 0 else "right_wink"
        return None


class FaceDispatcher:
    """Carries face events out through the shared action controller."""

    def __init__(self, controller, safety, smoothing: float = 0.6, gain: float = 3.0,
                 pointer_mode: str = "relative") -> None:
        self.controller = controller
        self.safety = safety
        self.smoothing = smoothing
        self.gain = gain
        self.pointer_mode = pointer_mode
        self.neutral: tuple[float, float] | None = None  # for "absolute", from the engine
        self._fx, self._fy = _OneEuroFilter(), _OneEuroFilter()
        self._last_move = 0.0
        self._prev: tuple[float, float, float] | None = None  # filtered head and time, for "relative"
        self._pos: list[float] | None = None
        self._scroll_rest = 0.0

    def dispatch(self, event: FaceEvent, now: float | None = None) -> DispatchResult | None:
        now = time.monotonic() if now is None else now
        controller = self.controller
        if event.trigger and event.action == "pause":
            paused = self.safety.toggle_pause()
            controller.release()
            return DispatchResult("Control Paused" if paused else "Control Resumed", "")
        if self.safety.paused or self.safety.locked or not controller.is_enabled("mouse_control"):
            self._prev = None
            return None
        if event.trigger:
            if event.action in ("left_click", "double_click", "right_click", "minimize_window"):
                return controller.run_action(event.action)
            if event.action == "scroll_mode":
                return DispatchResult("Scroll Mode On" if event.scroll_mode else "Scroll Mode Off", "mouse_control")
        if event.scroll:
            self._scroll_rest += event.scroll
            pixels = int(self._scroll_rest)
            if pixels:
                self._scroll_rest -= pixels
                controller.executor.scroll_pixels(0, pixels)
        if event.steer and event.head is not None:
            self._steer(event.head, now)
        else:
            self._prev = None  # holding still: the next movement starts from here
        return None

    def _filtered(self, head: tuple[float, float], now: float) -> tuple[float, float]:
        if now - self._last_move > 0.5:
            self._fx.reset()  # the face was away or the cursor was held: start afresh
            self._fy.reset()
        self._last_move = now
        if self.smoothing <= 0:
            return head
        cutoff = 3.0 * 10.0 ** (-min(0.95, self.smoothing) / 0.9)
        return self._fx(head[0], now, cutoff, 4.0), self._fy(head[1], now, cutoff, 4.0)

    def _steer(self, head: tuple[float, float], now: float) -> None:
        hx, hy = self._filtered(head, now)
        w, h = self.controller.screen_w, self.controller.screen_h
        executor = self.controller.executor
        if self.pointer_mode == "absolute":
            centre = self.neutral or (0.0, 0.0)
            x = 0.5 + (hx - centre[0]) * self.gain * 0.5
            y = 0.5 + (hy - centre[1]) * self.gain * 0.8  # heads nod less far than they turn
            self._pos = [min(1.0, max(0.0, x)) * w, min(1.0, max(0.0, y)) * h]
        else:
            # Like a mouse: the cursor moves by how much the head moved, faster for quick
            # movements (crossing the screen) and slower for slow ones (hitting a small target).
            if self._prev is None or now - self._prev[2] > 0.5:
                self._prev = (hx, hy, now)
                position = getattr(executor, "position", None)
                self._pos = list(position()) if position is not None and self._pos is None else (
                    self._pos or [w / 2.0, h / 2.0])
                return
            dt = max(1e-3, now - self._prev[2])
            dx, dy = hx - self._prev[0], hy - self._prev[1]
            self._prev = (hx, hy, now)
            speed = math.hypot(dx, dy) / dt
            accel = 0.35 + 1.65 * min(1.0, speed / 0.8)
            self._pos[0] = min(w - 1.0, max(0.0, self._pos[0] + dx * w * self.gain * accel * 0.5))
            self._pos[1] = min(h - 1.0, max(0.0, self._pos[1] + dy * h * self.gain * accel * 0.8))
        executor.move_mouse(min(w - 1, max(0, round(self._pos[0]))), min(h - 1, max(0, round(self._pos[1]))))


class FaceTracker:
    """Thin wrapper around MediaPipe's Face Landmarker (Tasks API), like HandTracker."""

    def __init__(self, model_path: Path) -> None:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        model_path = ensure_model(Path(model_path), FACE_MODEL_URL, "face-tracking", "~4 MB")
        try:
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=model_path.read_bytes()),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.6,
                min_face_presence_confidence=0.6,
                min_tracking_confidence=0.6,
                output_face_blendshapes=True,
            )
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:
            raise FaceError(f"Could not load the face-tracking model at {model_path}: {exc}") from exc
        self._last_timestamp_ms = 0

    def process(self, frame_bgr: np.ndarray) -> FaceResult | None:
        import mediapipe as mp

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = max(self._last_timestamp_ms + 1, int(time.monotonic() * 1000))
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.face_landmarks:
            return None
        points = np.array([[p.x, p.y, p.z] for p in result.face_landmarks[0]], dtype=np.float32)
        shapes = {}
        if result.face_blendshapes:
            shapes = {c.category_name: float(c.score) for c in result.face_blendshapes[0]}
        return FaceResult(points, shapes)

    @staticmethod
    def draw(frame_bgr: np.ndarray, result: FaceResult, event: FaceEvent | None = None,
             wink_threshold: float = 0.55) -> None:
        """Eye outlines (red while shut), the nose pointer and the lips, in place."""
        h, w = frame_bgr.shape[:2]
        lm = result.landmarks

        def pt(i):
            return int(lm[i][0] * w), int(lm[i][1] * h)

        levels = event.eyes if event is not None else (1.0, 1.0)
        for eye, other in ((EYE_A, EYE_B), (EYE_B, EYE_A)):
            corner_a, corner_b, upper, lower = eye
            ring = [corner_a, *upper, corner_b, *reversed(lower)]
            left = (lm[corner_a][0] + lm[corner_b][0]) < (lm[other[0]][0] + lm[other[1]][0])
            shut = levels[0 if left else 1] < wink_threshold
            colour = (60, 60, 230) if shut else (80, 220, 140)
            cv2.polylines(frame_bgr, [np.array([pt(i) for i in ring], np.int32)], True, colour, 2, cv2.LINE_AA)
        cv2.line(frame_bgr, pt(LIP_UPPER), pt(LIP_LOWER), (60, 170, 255), 2, cv2.LINE_AA)
        scrolling = event is not None and event.scroll_mode
        cv2.circle(frame_bgr, pt(NOSE_TIP), 7, (233, 236, 239), -1, cv2.LINE_AA)
        cv2.circle(frame_bgr, pt(NOSE_TIP), 7, (255, 170, 60) if scrolling else (46, 204, 113), 2, cv2.LINE_AA)

"""Hand Tracking Layer: detects hand landmarks using MediaPipe."""
from __future__ import annotations

import shutil
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)

# Bone connections between the 21 hand landmarks (same layout as the MediaPipe docs).
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


class TrackerError(RuntimeError):
    """Raised when the hand-landmark model can't be fetched or loaded."""


@dataclass
class HandResult:
    landmarks: np.ndarray  # shape (21, 3): normalized (x, y, z) per hand landmark
    handedness: str
    score: float
    other: "HandResult | None" = None  # a second hand in view, for two-hand gestures


def ensure_model(path: Path, url: str = MODEL_URL, what: str = "hand-tracking", size: str = "~8 MB") -> Path:
    """Returns the local model file, downloading it once if it isn't there yet.

    Only the model file is fetched - no camera data ever leaves the machine.
    """
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=30) as response, open(partial, "wb") as f:
            shutil.copyfileobj(response, f)
        partial.replace(path)
    except OSError as exc:
        raise TrackerError(
            f"Could not download the {what} model (one-time, {size}).\n"
            f"Check your internet connection, or download it manually:\n{url}\n"
            f"and save it as:\n{path}\n({exc})"
        ) from exc
    return path


class HandTracker:
    """Thin wrapper around MediaPipe's Hand Landmarker (Tasks API).

    Uses the Tasks API (the legacy `mp.solutions.hands` API is gone from newer
    MediaPipe releases), which needs the `hand_landmarker.task` model file (fetched
    once by `ensure_model`). requirements.txt pins MediaPipe to builds without the
    usage-statistics uploader that later releases bundle.
    """

    def __init__(
        self,
        model_path: Path,
        max_hands: int = 2,
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.6,
    ) -> None:
        model_path = ensure_model(Path(model_path))
        try:
            options = vision.HandLandmarkerOptions(
                # Passed as a buffer: avoids native path handling of spaces/non-ASCII.
                base_options=mp_python.BaseOptions(model_asset_buffer=model_path.read_bytes()),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=max_hands,
                min_hand_detection_confidence=min_detection_confidence,
                min_hand_presence_confidence=min_tracking_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self._landmarker = vision.HandLandmarker.create_from_options(options)
        except Exception as exc:  # MediaPipe raises plain RuntimeError/ValueError here
            raise TrackerError(f"Could not load the hand-tracking model at {model_path}: {exc}") from exc
        self._last_timestamp_ms = 0
        # "any", "left" or "right": which of the user's hands is allowed to control.
        self.preferred_hand = "any"

    def process(self, frame_bgr: np.ndarray) -> HandResult | None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # VIDEO mode requires strictly increasing timestamps.
        timestamp_ms = max(self._last_timestamp_ms + 1, int(time.monotonic() * 1000))
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            return None

        # Frames arrive mirrored (selfie view), which is what MediaPipe's handedness
        # labels assume - so "Left" here really is the user's left hand.
        hands = []
        for i, hand in enumerate(result.hand_landmarks):
            handedness, score = "", 1.0
            if i < len(result.handedness) and result.handedness[i]:
                category = result.handedness[i][0]
                handedness, score = category.category_name or "", float(category.score)
            points = np.array([[lm.x, lm.y, lm.z] for lm in hand], dtype=np.float32)
            hands.append(HandResult(landmarks=points, handedness=handedness, score=score))
        chosen = next((h for h in hands if self.preferred_hand in ("any", h.handedness.lower())), None)
        if chosen is None:
            return None  # only the other hand is in view: it must not control anything
        chosen.other = next((h for h in hands if h is not chosen), None)
        return chosen

    def draw(self, frame_bgr: np.ndarray, result: HandResult) -> None:
        """Draws landmark points, bone connections and a bounding box onto the frame, in place."""
        h, w = frame_bgr.shape[:2]
        pts = [(int(x * w), int(y * h)) for x, y, _ in result.landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame_bgr, pts[a], pts[b], (80, 220, 140), 2, cv2.LINE_AA)
        for x, y in pts:
            cv2.circle(frame_bgr, (x, y), 4, (233, 236, 239), -1, cv2.LINE_AA)
            cv2.circle(frame_bgr, (x, y), 4, (46, 204, 113), 1, cv2.LINE_AA)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pad = 18
        cv2.rectangle(
            frame_bgr,
            (max(0, min(xs) - pad), max(0, min(ys) - pad)),
            (min(w - 1, max(xs) + pad), min(h - 1, max(ys) + pad)),
            (80, 220, 140), 1, cv2.LINE_AA,
        )

    def close(self) -> None:
        self._landmarker.close()

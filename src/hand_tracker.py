"""Hand Tracking Layer: detects hand landmarks using MediaPipe."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np


@dataclass
class HandResult:
    landmarks: np.ndarray  # shape (21, 3): normalized (x, y, z) per hand landmark
    handedness: str
    score: float


class HandTracker:
    """Thin wrapper around MediaPipe's `solutions.hands` API.

    Deliberately uses the legacy `solutions` API rather than the newer Tasks API -
    its hand-landmark model ships bundled inside the `mediapipe` package, so detection
    works immediately after `pip install mediapipe` with no extra model download.
    """

    def __init__(
        self,
        max_hands: int = 1,
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.6,
    ) -> None:
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    def process(self, frame_bgr: np.ndarray) -> HandResult | None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self._hands.process(rgb)
        if not result.multi_hand_landmarks:
            return None

        hand_landmarks = result.multi_hand_landmarks[0]
        handedness_info = result.multi_handedness[0].classification[0]
        points = np.array(
            [[lm.x, lm.y, lm.z] for lm in hand_landmarks.landmark], dtype=np.float32
        )
        return HandResult(landmarks=points, handedness=handedness_info.label, score=handedness_info.score)

    def draw(self, frame_bgr: np.ndarray, result: HandResult) -> None:
        """Draws landmark points + bone connections directly onto the frame, in place."""
        h, w = frame_bgr.shape[:2]
        pts = [(int(x * w), int(y * h)) for x, y, _ in result.landmarks]
        for a, b in self._mp_hands.HAND_CONNECTIONS:
            cv2.line(frame_bgr, pts[a], pts[b], (80, 220, 140), 2, cv2.LINE_AA)
        for x, y in pts:
            cv2.circle(frame_bgr, (x, y), 4, (233, 236, 239), -1, cv2.LINE_AA)
            cv2.circle(frame_bgr, (x, y), 4, (46, 204, 113), 1, cv2.LINE_AA)

    def close(self) -> None:
        self._hands.close()

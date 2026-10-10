"""Custom gestures: a small nearest-neighbour classifier over landmark features.

The user records a few dozen samples of a hand pose under a name; `predict` then
matches a live hand against those samples. Only numeric landmark features are stored -
never images or video.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

WRIST, MIDDLE_MCP = 0, 9
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,23}$")


class CustomGestureError(ValueError):
    """Raised for an invalid gesture name or unusable samples."""


def landmark_features(landmarks: np.ndarray) -> np.ndarray:
    """21 (x, y) points relative to the wrist, scaled by palm size and rotated so the
    palm points up - so the features don't change with where the hand is in the frame,
    how close it is to the camera, or how it is tilted."""
    pts = np.asarray(landmarks, dtype=np.float32)[:, :2]
    pts = pts - pts[WRIST]
    scale = max(float(np.hypot(*pts[MIDDLE_MCP])), 1e-4)
    ux, uy = pts[MIDDLE_MCP] / scale
    rotation = np.array([[-uy, ux], [-ux, -uy]], dtype=np.float32)  # maps wrist->palm onto (0, -1)
    return ((pts / scale) @ rotation.T).reshape(-1)


class CustomGestureStore:
    def __init__(self, path: Path | None = None, threshold: float = 0.22, max_landmark_distance: float = 0.5,
                 k: int = 3) -> None:
        self._path = path
        # A pose matches a sample when the landmarks are on average within `threshold`
        # palm lengths AND no single landmark is further off than `max_landmark_distance`.
        # The second test matters: one finger up vs. down barely moves the average.
        self.threshold = threshold
        self.max_landmark_distance = max_landmark_distance
        self.k = k
        self._samples: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for name, rows in data.items() if isinstance(data, dict) else []:
            array = np.asarray(rows, dtype=np.float32)
            if array.ndim == 2 and array.shape[1] == 42 and len(array) > 0:
                self._samples[name] = array

    def save(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({name: np.round(rows, 4).tolist() for name, rows in self._samples.items()}, f)
        except OSError:
            pass

    def names(self) -> list[str]:
        return list(self._samples)

    def count(self, name: str) -> int:
        return len(self._samples.get(name, ()))

    @staticmethod
    def validate_name(name: str) -> str:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise CustomGestureError("Use 1-24 letters, digits, spaces, '-' or '_' for the name.")
        return name

    def add_samples(self, name: str, landmark_sets: list[np.ndarray]) -> int:
        """Adds samples for a gesture (creating it if new). Returns its new sample count."""
        name = self.validate_name(name)
        if not landmark_sets:
            raise CustomGestureError("No hand was visible while recording.")
        rows = np.stack([landmark_features(lm) for lm in landmark_sets])
        if name in self._samples:
            rows = np.concatenate([self._samples[name], rows])
        self._samples[name] = rows
        self.save()
        return len(rows)

    def remove(self, name: str) -> None:
        if self._samples.pop(name, None) is not None:
            self.save()

    def predict(self, landmarks: np.ndarray) -> str | None:
        """Name of the closest trained gesture, or None if nothing is close enough."""
        if not self._samples:
            return None
        query = landmark_features(landmarks).reshape(21, 2)
        best_name, best_distance = None, self.threshold
        for name, rows in self._samples.items():
            per_landmark = np.linalg.norm(rows.reshape(-1, 21, 2) - query, axis=2)
            per_sample = per_landmark.mean(axis=1)
            per_sample[per_landmark.max(axis=1) > self.max_landmark_distance] = np.inf
            nearest = np.sort(per_sample)[: self.k]
            distance = float(nearest.mean())
            if distance < best_distance:
                best_name, best_distance = name, distance
        return best_name

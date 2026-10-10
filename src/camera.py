"""Camera Layer: reads frames from the webcam."""
from __future__ import annotations

import platform

import cv2
import numpy as np


class CameraError(RuntimeError):
    """Raised when the webcam cannot be opened or read from."""


class Camera:
    def __init__(self, index: int = 0, width: int = 960, height: int = 540) -> None:
        self._index = index
        self._width = width
        self._height = height
        self._cap: cv2.VideoCapture | None = None

    def start(self) -> None:
        if platform.system() == "Windows":
            cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(self._index)

        if not cap.isOpened():
            raise CameraError(
                f"Could not open webcam at index {self._index}. "
                "Check that it's connected and not in use by another app, and that "
                "this app has camera permission."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap = cap

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            raise CameraError("Camera has not been started.")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return cv2.flip(frame, 1)  # Mirror the feed so it behaves like looking in a mirror.

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

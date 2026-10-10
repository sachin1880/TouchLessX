"""Troubleshooting log and session statistics.

`ActionLog` appends one text line per performed action to logs/actions.log (gesture,
action, confidence - never any image data). `SessionStats` keeps in-memory counters for
the dashboard's analytics and the assistant's suggestions; nothing in it is persisted.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path


class ActionLog:
    def __init__(self, path: Path | None, enabled: bool = True, max_bytes: int = 512_000) -> None:
        self.path = path
        self.enabled = enabled
        self._max_bytes = max_bytes

    def record(self, gesture: str, action: str, confidence: float) -> None:
        if not self.enabled or self.path is None:
            return
        line = (
            f"{datetime.now().isoformat(timespec='seconds')}  gesture={gesture}  "
            f"action={action}  confidence={confidence:.0f}\n"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self._max_bytes:
                self.path.replace(self.path.with_suffix(".log.1"))  # keep one older file
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Logging must never break gesture control.

    def clear(self) -> None:
        if self.path is None:
            return
        for path in (self.path, self.path.with_suffix(".log.1")):
            try:
                path.unlink()
            except OSError:
                pass


class FrameLog:
    """Per-frame diagnostics for tuning recognition on real use: what the hand looked
    like to the classifier (21 landmark points and the resulting labels) and what was
    done about it. Numbers only - never an image. Covered by the same "diagnostic log"
    switch as the action log, capped in size, and deleted by "Clear log"."""

    HEADER = ["time", "fps", "raw", "gesture", "trigger", "hold", "confidence", "pinch_gap", "action"] + [
        f"{axis}{i}" for i in range(21) for axis in "xy"]

    def __init__(self, path: Path | None, enabled: bool = True, max_bytes: int = 6_000_000) -> None:
        self.path = path
        self.enabled = enabled
        self._max_bytes = max_bytes
        self._rows: list[str] = []
        self._last_flush = time.monotonic()

    def record(self, event, raw: str, landmarks, fps: float, pinch_gap: float, action: str = "") -> None:
        if not self.enabled or self.path is None or landmarks is None:
            return
        points = ",".join(f"{value:.3f}" for point in landmarks for value in point[:2])
        self._rows.append(
            f"{time.time():.3f},{fps:.0f},{raw},{event.name},{int(event.is_trigger)},{event.hold_time:.2f},"
            f"{event.confidence:.0f},{pinch_gap:.3f},{action},{points}\n")
        if time.monotonic() - self._last_flush > 3.0:
            self.flush()

    def flush(self) -> None:
        self._last_flush = time.monotonic()
        rows, self._rows = self._rows, []
        if not rows or self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self._max_bytes:
                self.path.replace(self.path.with_suffix(".csv.1"))  # keep one older file
            new = not self.path.exists()
            with open(self.path, "a", encoding="utf-8") as f:
                if new:
                    f.write(",".join(self.HEADER) + "\n")
                f.writelines(rows)
        except OSError:
            pass  # diagnostics must never break gesture control

    def clear(self) -> None:
        self._rows = []
        if self.path is None:
            return
        for path in (self.path, self.path.with_suffix(".csv.1")):
            try:
                path.unlink()
            except OSError:
                pass


class SessionStats:
    def __init__(self, timeline_seconds: int = 120) -> None:
        self.started = time.monotonic()
        self.frames = 0
        self.hand_frames = 0
        self.recognized_frames = 0
        self.total_gestures = 0
        self.fps = 0.0
        self.gesture_counts: Counter = Counter()
        self.timeline: deque = deque(maxlen=timeline_seconds)  # avg confidence per second
        self._confidence_sum = 0.0
        self._latency_sum = 0.0
        self._last_gesture = "none"
        self._bucket_second: int | None = None
        self._bucket_sum = 0.0
        self._bucket_n = 0

    def record_frame(self, event, latency_ms: float, fps: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.frames += 1
        self.fps = fps
        self._latency_sum += latency_ms

        second = int(now)
        if self._bucket_second is None:
            self._bucket_second = second
        while self._bucket_second < second:
            self.timeline.append(self._bucket_sum / self._bucket_n if self._bucket_n else 0.0)
            self._bucket_sum, self._bucket_n = 0.0, 0
            self._bucket_second += 1
            if second - self._bucket_second > self.timeline.maxlen:
                self._bucket_second = second  # long gap (camera stopped): don't backfill forever

        if event.name not in ("none", "resting"):
            self.hand_frames += 1
            self._confidence_sum += event.confidence
            self._bucket_sum += event.confidence
            self._bucket_n += 1
            if event.name != "unknown":
                self.recognized_frames += 1
                if event.is_trigger and event.name != self._last_gesture:
                    self.total_gestures += 1
                    self.gesture_counts[event.name] += 1
        self._last_gesture = event.name

    @property
    def avg_confidence(self) -> float:
        return self._confidence_sum / self.hand_frames if self.hand_frames else 0.0

    @property
    def accuracy(self) -> float:
        """Share of frames with a hand in view whose pose was a recognised gesture."""
        return 100.0 * self.recognized_frames / self.hand_frames if self.hand_frames else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self._latency_sum / self.frames if self.frames else 0.0

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)


def build_suggestions(
    stats: SessionStats,
    *,
    camera_ok: bool,
    input_ok: bool,
    paused: bool,
    custom_gestures: int = 0,
    gesture_label=lambda g: g,
    brief_poses=None,
) -> list[str]:
    """Plain rule-based tips derived from this session's local statistics."""
    tips: list[str] = []
    if not camera_ok:
        tips.append("The camera isn't running. Press Start Camera in the header; if it fails, "
                    "check the camera permission for the app you launched TouchlessX from.")
    if not input_ok:
        tips.append("Mouse/keyboard control is not permitted yet, so gestures are detected but nothing "
                    "happens. Grant input (Accessibility) permission under System Controls.")
    if paused:
        tips.append("Control is paused. Press Space or the Resume button to let gestures act again.")
    if camera_ok and stats.hand_frames < 60:
        tips.append("Hold your hand in view for a few seconds so tracking quality can be assessed.")
        return tips

    if stats.hand_frames >= 60:
        if stats.avg_confidence < 75:
            tips.append(f"Tracking confidence is averaging {stats.avg_confidence:.0f}%. Add light in front of "
                        "you and keep your whole hand inside the frame.")
        if stats.accuracy < 70:
            tips.append(f"Only {stats.accuracy:.0f}% of frames matched a known gesture. Face your palm to the "
                        "camera and hold poses more distinctly, or train the pose as a custom gesture.")
        if 0 < stats.fps < 15:
            tips.append(f"The frame rate is low ({stats.fps:.0f} FPS). Pick a lower camera resolution in "
                        "Settings for snappier control.")
        if stats.avg_latency_ms > 60:
            tips.append(f"Processing takes {stats.avg_latency_ms:.0f} ms per frame. Close heavy apps or lower "
                        "the camera resolution.")
        if stats.gesture_counts:
            name, count = stats.gesture_counts.most_common(1)[0]
            tips.append(f"Your most used gesture this session is {gesture_label(name)} ({count} times).")
        for name, count in (brief_poses.most_common(2) if brief_poses else []):
            if count >= 5 and name != "pinch":
                tips.append(f"{gesture_label(name)} was shown {count} times but let go before it fired. If those "
                            "were meant, lower 'Pose hold time' in Gesture Studio; if not, it is working as "
                            "intended.")
        if custom_gestures == 0:
            tips.append("You can teach TouchlessX your own hand pose below and map it to any action.")
    if not tips:
        tips.append("Everything looks good. Tracking is confident and responsive.")
    return tips

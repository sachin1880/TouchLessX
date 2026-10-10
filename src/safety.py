"""Safety: the global pause switch and the confidence gate.

Per-gesture cooldowns (to stop a held pinch firing a click every frame) live in
`gesture_engine.GestureEngine` since they need per-gesture timing state; this module
holds the cross-cutting safeguards that apply to every gesture equally.
"""
from __future__ import annotations


class SafetyGuard:
    def __init__(self, confidence_threshold: float = 0.6) -> None:
        self.confidence_threshold = confidence_threshold
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def toggle_pause(self) -> bool:
        self._paused = not self._paused
        return self._paused

    def set_paused(self, value: bool) -> None:
        self._paused = value

    def passes_confidence(self, confidence_pct: float) -> bool:
        """FR-06: ignore low-confidence/unstable detections where feasible."""
        return confidence_pct >= self.confidence_threshold * 100

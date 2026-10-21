"""Theme configuration for all 12 Jarvis agent states.

Each AgentTheme carries the visual parameters used by AvatarCanvas and
AgentWindow.  Import ``get(state)`` to look up a theme by state name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

Color = Tuple[int, int, int]


@dataclass(frozen=True)
class AgentTheme:
    state:            str
    label:            str    # status-chip text
    ring_rgb:         Color  # main ring dots
    ring_bright_rgb:  Color  # front-facing (bright) ring dots
    accent_hex:       str    # hex accent for UI labels / borders
    speed:            float  # avatar rotation speed (deg/frame)
    brightness_base:  float  # minimum brightness multiplier
    brightness_range: float  # pulse amplitude on top of base
    modulate:         bool   # apply wave/pulse ring modulation
    face_intensity:   float  # golden core multiplier


# ── 12 states ─────────────────────────────────────────────────────────────────
THEMES: dict[str, AgentTheme] = {
    "idle": AgentTheme(
        "idle",      "◉  READY",
        (0, 188, 212),   (0, 229, 255),   "#00d4ff",
        0.6,  0.50, 0.22, False, 0.5,
    ),
    "listening": AgentTheme(
        "listening", "◉  LISTENING",
        (0, 200, 240),   (80, 240, 255),  "#00d4ff",
        1.3,  0.75, 0.55, True,  0.9,
    ),
    "thinking": AgentTheme(
        "thinking",  "⟳  PROCESSING",
        (0, 188, 212),   (80, 220, 255),  "#00d4ff",
        2.2,  0.65, 0.40, True,  0.6,
    ),
    "speaking": AgentTheme(
        "speaking",  "◈  SPEAKING",
        (0, 188, 212),   (0, 229, 255),   "#ffc844",
        1.6,  0.80, 0.25, True,  1.0,
    ),
    "coding": AgentTheme(
        "coding",    "⌨  CODING",
        (60, 220, 120),  (100, 255, 160), "#4de6a0",
        1.0,  0.70, 0.35, True,  0.6,
    ),
    "research": AgentTheme(
        "research",  "◎  RESEARCH",
        (138, 100, 240), (180, 140, 255), "#8a64f0",
        1.2,  0.65, 0.35, True,  0.6,
    ),
    "executing": AgentTheme(
        "executing", "▶  EXECUTING",
        (255, 140, 40),  (255, 190, 80),  "#ff8c28",
        1.8,  0.80, 0.30, True,  0.7,
    ),
    "success": AgentTheme(
        "success",   "✓  SUCCESS",
        (60, 220, 100),  (100, 255, 140), "#50e664",
        0.8,  1.00, 0.40, True,  1.2,
    ),
    "error": AgentTheme(
        "error",     "⚠  ERROR",
        (255, 70, 70),   (255, 140, 140), "#ff4646",
        2.0,  0.75, 0.35, True,  0.5,
    ),
    "meeting": AgentTheme(
        "meeting",   "◉  MEETING",
        (80, 160, 255),  (120, 200, 255), "#50a0ff",
        0.7,  0.65, 0.25, False, 0.6,
    ),
    "phone": AgentTheme(
        "phone",     "☎  CALL",
        (60, 220, 120),  (100, 255, 160), "#4de6a0",
        1.2,  0.80, 0.35, True,  0.7,
    ),
    "focus": AgentTheme(
        "focus",     "◉  FOCUS",
        (0, 100, 130),   (0, 150, 180),   "#007896",
        0.4,  0.35, 0.15, False, 0.3,
    ),
}


def get(state: str) -> AgentTheme:
    """Return the theme for *state*, falling back to 'idle'."""
    return THEMES.get(state, THEMES["idle"])

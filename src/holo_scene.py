"""Full-screen Holo mode: what is on screen and how the hands work it. No Tk here - the
window (ui_holo_mode.py) feeds hands in, performs the actions this returns, and shows
the image `render()` makes - so all of this is testable without a display.

In the middle floats a 3D hologram - a globe, an atom, DNA, a knot or an arc reactor -
that the bare hands handle like a real object:
* pinch empty space and move: it turns with the hand; let go mid-move and it keeps
  spinning, slowing down like a real flywheel;
* pinch with both hands: pull apart / push together to scale it, twist to roll it;
* make a fist: it bursts into glowing pieces; open the hand and they fly back together;
* glowing particles orbit it, and the index fingertip pushes them out of the way.
Around it, panels (time, system, media, models) work by pinching their buttons, and move
by pinching their title bar. The mouse pointer is never touched.

Rendering: the mirrored camera image, darkened and tinted, is the room; the hologram,
the particles and the hand skeleton glow (drawn at half size, blurred, added); panels are
truly see-through (alpha blended). A full-screen frame takes a few tens of milliseconds.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .gesture_engine import pinch_distance

CYAN = (0, 214, 255)
AMBER = (255, 184, 64)
GREEN = (80, 230, 160)
RED = (255, 90, 90)
WHITE = (232, 246, 255)
DIM = (120, 170, 200)
PANEL_FILL = (6, 22, 44, 150)
TITLE_H = 34
GLOW = 10                  # a panel's image is this much bigger on every side, for its glow
EXIT_HOLD = 0.8            # seconds of both palms to leave
PINCH_RELEASE = 1.25       # pinch ends once the gap exceeds threshold x this
ACTIVE = (0.10, 0.08, 0.90, 0.80)   # the part of the camera image mapped to the whole screen
BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))
THUMB_TIP, INDEX_TIP = 4, 8

# The hologram's physics
TURN_PER_PX = 0.008        # radians of turn per screen pixel of hand movement
SPIN_DECAY = 1.1           # per second: how fast a flicked spin slows down
IDLE_SPIN = 0.22           # radians/second when nobody is touching it
MAX_SPIN = 9.0
SCALE_RANGE = (0.35, 2.6)
EXPLODE_RATE = 2.6         # explode amount per second, towards 0 or 1
PARTICLES = 240
PUSH_RADIUS = 90           # px around the fingertip that pushes particles
MODELS = ("globe", "atom", "dna", "knot", "reactor")
THROW_FRICTION = 1.3       # per second, on a thrown hologram
BOUNCE = 0.72              # speed kept when it bounces off a screen edge
HOME_AFTER = 0.6           # seconds of slow drift before a thrown hologram floats home
CHARGE_START = 0.25        # seconds of still open palm before the repulsor starts charging
CHARGE_FULL = 1.0          # ... and when it fires
BLAST_COOLDOWN = 1.4
STILL_SPEED = 260          # px/s: a palm slower than this counts as held still
TRAIL_SECONDS = 0.45
BOOT_SECONDS = 1.8

_FONT_DIR = Path("/System/Library/Fonts")
_fonts: dict = {}


def font(size: int, kind: str = "body") -> ImageFont.FreeTypeFont:
    """Mac system fonts when present (DIN for headings and numbers), Pillow's otherwise."""
    key = (size, kind)
    if key not in _fonts:
        files = {"head": [_FONT_DIR / "Supplemental" / "DIN Alternate Bold.ttf", _FONT_DIR / "SFNS.ttf"],
                 "body": [_FONT_DIR / "SFNS.ttf", _FONT_DIR / "Helvetica.ttc"]}[kind]
        chosen = None
        for path in files:
            try:
                chosen = ImageFont.truetype(str(path), size)
                break
            except OSError:
                continue
        _fonts[key] = chosen or ImageFont.load_default(size=size)
    return _fonts[key]


# ------------------------------------------------------------------ hands
class OneEuro:
    """The One Euro filter (Casiez et al.): little smoothing at speed, a lot when still."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.004, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x: float | None = None
        self._dx = 0.0
        self._t = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self._x = None

    def __call__(self, x: float, t: float) -> float:
        if self._x is None:
            self._x, self._dx, self._t = x, 0.0, t
            return x
        dt = max(t - self._t, 1e-3)
        self._t = t
        dx = (x - self._x) / dt
        self._dx += self._alpha(self.d_cutoff, dt) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x += self._alpha(cutoff, dt) * (x - self._x)
        return self._x


@dataclass
class Hand:
    present: bool = False
    x: float = 0.0             # the pinch point, on screen
    y: float = 0.0
    pinching: bool = False
    skeleton: list = field(default_factory=list)   # 21 screen points, or [] when no hand
    tip: tuple = (0.0, 0.0)    # the index fingertip, on screen
    second: "Hand | None" = None


class _OneHand:
    def __init__(self, owner: "HandInput") -> None:
        self.owner = owner
        self.fx, self.fy = OneEuro(), OneEuro()
        self.pinching = False

    def update(self, landmarks: np.ndarray | None, t: float) -> Hand:
        if landmarks is None:
            self.fx.reset()
            self.fy.reset()
            self.pinching = False
            return Hand()
        gap = pinch_distance(landmarks)
        limit = self.owner.threshold * (PINCH_RELEASE if self.pinching else 1.0)
        self.pinching = gap < limit
        mid = (landmarks[THUMB_TIP][:2] + landmarks[INDEX_TIP][:2]) / 2.0
        x, y = self.owner.to_screen(float(mid[0]), float(mid[1]))
        skeleton = [self.owner.to_screen(float(p[0]), float(p[1])) for p in landmarks]
        return Hand(True, self.fx(x, t), self.fy(y, t), self.pinching, skeleton, skeleton[INDEX_TIP])


class HandInput:
    """Landmarks (normalized, mirrored like a selfie) -> steady pointers on the screen and
    pinches with hysteresis, for the main hand and, when it is in view, the other one."""

    def __init__(self, width: int, height: int, pinch_threshold: float = 0.4) -> None:
        self.width, self.height = width, height
        self.threshold = pinch_threshold
        self._main, self._other = _OneHand(self), _OneHand(self)

    def to_screen(self, nx: float, ny: float) -> tuple[float, float]:
        left, top, right, bottom = ACTIVE
        x = (nx - left) / (right - left) * self.width
        y = (ny - top) / (bottom - top) * self.height
        return min(max(x, 0.0), self.width - 1.0), min(max(y, 0.0), self.height - 1.0)

    def update(self, landmarks: np.ndarray | None, t: float, other: np.ndarray | None = None) -> Hand:
        hand = self._main.update(landmarks, t)
        second = self._other.update(other if landmarks is not None else None, t)
        if hand.present and second.present:
            hand.second = second
        return hand


# ------------------------------------------------------------------ the 3D models
def _circle(radius: float, n: int = 64, z: float = 0.0) -> np.ndarray:
    a = np.linspace(0, 2 * np.pi, n + 1)
    return np.stack([radius * np.cos(a), radius * np.sin(a), np.full_like(a, z)], axis=1)


def _rotate(points: np.ndarray, yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> np.ndarray:
    return points @ rotation(yaw, pitch, roll).T


def rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    cy, sy, cp, sp, cr, sr = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), math.sin(roll)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return rz @ rx @ ry


def build_model(name: str) -> list[np.ndarray]:
    """A model as polylines (arrays of 3D points) inside the unit sphere."""
    lines: list[np.ndarray] = []
    if name == "globe":
        for lat in range(-60, 61, 20):
            r, z = math.cos(math.radians(lat)), math.sin(math.radians(lat))
            ring = _circle(r, 72)
            lines.append(np.stack([ring[:, 0], np.full(len(ring), z), ring[:, 1]], axis=1))
        for lon in range(0, 180, 20):
            ring = _circle(1.0, 72)
            meridian = np.stack([ring[:, 0], ring[:, 1], np.zeros(len(ring))], axis=1)
            lines.append(_rotate(meridian, yaw=math.radians(lon)))
        lines.append(_rotate(_circle(1.35, 96), pitch=math.radians(72), roll=math.radians(18)))   # an orbit ring
    elif name == "atom":
        for lat in (-45, 0, 45):
            r, z = 0.22 * math.cos(math.radians(lat)), 0.22 * math.sin(math.radians(lat))
            ring = _circle(r, 24)
            lines.append(np.stack([ring[:, 0], np.full(len(ring), z), ring[:, 1]], axis=1))
        for lon in (0, 60, 120):
            lines.append(_rotate(_circle(0.22, 24), yaw=math.radians(lon)))
        for tilt in (0, 60, 120):
            orbit = _circle(1.0, 96) * np.array([1.0, 0.38, 1.0])
            lines.append(_rotate(orbit, roll=math.radians(tilt)))
    elif name == "dna":
        steps = 160
        a = np.linspace(0, 5 * np.pi, steps)
        y = np.linspace(-1.1, 1.1, steps)
        strand1 = np.stack([0.42 * np.cos(a), y, 0.42 * np.sin(a)], axis=1)
        strand2 = np.stack([0.42 * np.cos(a + np.pi), y, 0.42 * np.sin(a + np.pi)], axis=1)
        lines += [strand1, strand2]
        for i in range(0, steps, 8):
            lines.append(np.stack([strand1[i], strand2[i]]))
    elif name == "knot":   # a (2, 3) torus knot - the trefoil
        a = np.linspace(0, 2 * np.pi, 360)
        r = 0.62 + 0.3 * np.cos(3 * a)
        core = np.stack([r * np.cos(2 * a), r * np.sin(2 * a), 0.3 * np.sin(3 * a)], axis=1)
        lines.append(core)
        lines.append(core * 0.94)
    else:                  # the arc reactor
        for radius, z in ((1.0, 0.0), (0.92, 0.04), (0.7, 0.08), (0.48, 0.12), (0.26, 0.16)):
            lines.append(_circle(radius, 96, z))
        for i in range(10):
            a0, a1 = math.radians(i * 36 + 4), math.radians(i * 36 + 30)
            lines.append(np.array([[0.52 * math.cos(a0), 0.52 * math.sin(a0), 0.1],
                                   [0.88 * math.cos(a0), 0.88 * math.sin(a0), 0.04],
                                   [0.88 * math.cos(a1), 0.88 * math.sin(a1), 0.04],
                                   [0.52 * math.cos(a1), 0.52 * math.sin(a1), 0.1],
                                   [0.52 * math.cos(a0), 0.52 * math.sin(a0), 0.1]]))
        tri = np.array([[0, 0.24, 0.18], [0.21, -0.12, 0.18], [-0.21, -0.12, 0.18], [0, 0.24, 0.18]])
        lines.append(tri)
    return [line.astype(np.float32) for line in lines]


def shatter(lines: list[np.ndarray], size: int = 7, seed: int = 7) -> tuple[list[np.ndarray], np.ndarray]:
    """The polylines cut into short pieces (sharing their end points, so whole again when
    put back), and the direction each piece flies when the model explodes."""
    rng = np.random.default_rng(seed)
    pieces, directions = [], []
    for line in lines:
        for start in range(0, max(len(line) - 1, 1), size):
            piece = line[start:start + size + 1]
            if len(piece) < 2:
                continue
            centre = piece.mean(axis=0)
            norm = float(np.linalg.norm(centre))
            outward = centre / norm if norm > 1e-3 else rng.normal(size=3)
            direction = outward + rng.normal(scale=0.35, size=3)
            pieces.append(piece)
            directions.append(direction / max(np.linalg.norm(direction), 1e-3) * rng.uniform(0.6, 1.4))
    return pieces, np.array(directions, np.float32)


class Hologram:
    """The object in the middle: its model, orientation, spin, size and explosion."""

    def __init__(self, model: str = "globe") -> None:
        self.yaw, self.pitch, self.roll = 0.6, -0.35, 0.0
        self.spin = np.array([IDLE_SPIN, 0.0], np.float32)   # yaw, pitch radians/second
        self.scale = 1.0
        self.explode = 1.0          # it assembles itself when Holo mode opens
        self.explode_target = 0.0
        self.grabbed = False
        self.offset = np.zeros(2, np.float32)    # px from its home in the middle
        self.velocity = np.zeros(2, np.float32)  # px/s when thrown
        self.carried = False
        self._slow_for = 0.0
        self.set_model(model)

    def set_model(self, name: str) -> None:
        self.model = name if name in MODELS else "globe"
        self.pieces, self.directions = shatter(build_model(self.model))

    def step(self, dt: float) -> None:
        if not self.grabbed:
            self.yaw += float(self.spin[0]) * dt
            self.pitch += float(self.spin[1]) * dt
            decay = math.exp(-SPIN_DECAY * dt)
            yaw_spin = float(self.spin[0])
            sign = 1.0 if yaw_spin >= 0 else -1.0
            if abs(yaw_spin) > IDLE_SPIN:   # only the speed above the idle turn dies away
                yaw_spin = sign * (IDLE_SPIN + (abs(yaw_spin) - IDLE_SPIN) * decay)
            else:                           # and a stopped one picks the idle turn back up
                yaw_spin += (sign * IDLE_SPIN - yaw_spin) * min(1.0, dt * 0.8)
            self.spin[0] = yaw_spin
            self.spin[1] *= decay
            self.pitch += (-0.35 - self.pitch) * min(1.0, dt * 0.15) if abs(self.spin[1]) < 0.05 else 0.0
        towards = self.explode_target - self.explode
        self.explode += max(-EXPLODE_RATE * dt, min(EXPLODE_RATE * dt, towards))

    def fly(self, dt: float, bounds: tuple[float, float, float, float]) -> bool:
        """Moves a thrown hologram: friction, bounces off the screen edges (bounds are the
        offsets it may reach: min x, min y, max x, max y), then a float home once it is
        slow. Returns True on a bounce, for the sound."""
        if self.carried or dt <= 0:
            return False
        bounced = False
        self.offset += self.velocity * dt
        for axis, (low, high) in enumerate(((bounds[0], bounds[2]), (bounds[1], bounds[3]))):
            if self.offset[axis] < low or self.offset[axis] > high:
                self.offset[axis] = min(max(self.offset[axis], low), high)
                if abs(self.velocity[axis]) > 80:
                    bounced = True
                self.velocity[axis] *= -BOUNCE
        self.velocity *= math.exp(-THROW_FRICTION * dt)
        if float(np.linalg.norm(self.velocity)) < 60:
            self._slow_for += dt
            if self._slow_for > HOME_AFTER:   # glide back to the middle
                self.offset *= math.exp(-2.5 * dt)
                self.velocity *= 0.5
        else:
            self._slow_for = 0.0
        return bounced

    def turn(self, dx: float, dy: float, dt: float) -> None:
        """Turn by a hand movement of (dx, dy) px; remember the speed for the flick."""
        dyaw, dpitch = dx * TURN_PER_PX, dy * TURN_PER_PX
        self.yaw += dyaw
        self.pitch = max(-1.5, min(1.5, self.pitch + dpitch))
        if dt > 0:
            measured = np.array([dyaw / dt, dpitch / dt], np.float32)
            self.spin = np.clip(self.spin * 0.5 + measured * 0.5, -MAX_SPIN, MAX_SPIN)

    def project(self, cx: float, cy: float, size: float, t: float) -> list[tuple[np.ndarray, float]]:
        """Each piece as 2D screen points with a brightness (front = bright)."""
        matrix = rotation(self.yaw, self.pitch, self.roll).T
        spread = self.explode * self.explode * 1.6   # eases out: a burst, not a drift
        wobble = math.sin(t * 3.0) * 0.04 * self.explode
        out = []
        for piece, direction in zip(self.pieces, self.directions):
            points = piece + direction * (spread + wobble)
            rotated = (points @ matrix) * self.scale
            depth = 3.6 / (3.6 - rotated[:, 2])
            screen = np.stack([cx + rotated[:, 0] * depth * size, cy - rotated[:, 1] * depth * size], axis=1)
            light = float(np.clip(0.55 + 0.45 * rotated[:, 2].mean() / max(self.scale, 0.3), 0.18, 1.0))
            out.append((screen.astype(np.int32), light))
        return out

    def sparks(self, cx: float, cy: float, size: float, t: float) -> list[tuple[int, int]]:
        """Moving highlights: electrons on the atom's orbits, a pulse along the others."""
        matrix = rotation(self.yaw, self.pitch, self.roll).T
        points = []
        if self.model == "atom":
            for i, tilt in enumerate((0, 60, 120)):
                a = t * (2.2 + i * 0.5) + i * 2.1
                p = np.array([[math.cos(a), 0.38 * math.sin(a), 0.0]], np.float32)
                points.append(_rotate(p, roll=math.radians(tilt))[0])
        elif self.model == "dna":
            for k in range(3):
                s = ((t * 0.35 + k / 3) % 1.0)
                a, y = s * 5 * math.pi, -1.1 + 2.2 * s
                points.append(np.array([0.42 * math.cos(a), y, 0.42 * math.sin(a)]))
        else:
            a = t * 1.6
            points.append(np.array([math.cos(a), 0.0, math.sin(a)]) * (0.26 if self.model == "reactor" else 1.0))
        out = []
        for p in points:
            r = (np.asarray(p, np.float32) @ matrix) * self.scale
            depth = 3.6 / (3.6 - r[2])
            out.append((int(cx + r[0] * depth * size), int(cy - r[1] * depth * size)))
        return out


class Particles:
    """An energy field orbiting the hologram; a fingertip pushes particles aside and they
    drift back."""

    def __init__(self, count: int = PARTICLES, seed: int = 3) -> None:
        rng = np.random.default_rng(seed)
        self.radius = rng.uniform(1.15, 1.75, count).astype(np.float32)
        self.angle = rng.uniform(0, 2 * np.pi, count).astype(np.float32)
        self.speed = rng.uniform(0.15, 0.55, count).astype(np.float32) * rng.choice([-1, 1], count)
        self.tilt = rng.normal(0, 0.35, count).astype(np.float32)
        self.offset = np.zeros((count, 2), np.float32)

    def positions(self, cx: float, cy: float, size: float, scale: float) -> np.ndarray:
        r = self.radius * scale
        x = cx + np.cos(self.angle) * r * size
        y = cy + (np.sin(self.angle) * self.tilt + np.sin(self.angle * 2 + self.tilt) * 0.08) * r * size
        return np.stack([x, y], axis=1) + self.offset

    def step(self, dt: float, cx: float, cy: float, size: float, scale: float,
             fingertip: tuple[float, float] | None) -> np.ndarray:
        self.angle += self.speed * dt
        positions = self.positions(cx, cy, size, scale)
        if fingertip is not None:
            away = positions - np.array(fingertip, np.float32)
            dist = np.linalg.norm(away, axis=1, keepdims=True)
            near = dist < PUSH_RADIUS
            push = np.where(near, away / np.maximum(dist, 1.0) * (PUSH_RADIUS - dist) * 0.5, 0.0)
            self.offset += push
            positions = positions + push
        self.offset *= math.exp(-2.2 * dt)   # drift back to the orbit
        return positions


# ------------------------------------------------------------------ panels
@dataclass
class Button:
    id: str
    text: str
    rect: tuple[int, int, int, int]   # x, y, w, h inside the panel
    action: str
    accent: tuple = CYAN


@dataclass
class Panel:
    id: str
    title: str
    x: float
    y: float
    w: int
    h: int
    buttons: list[Button] = field(default_factory=list)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def local(self, px: float, py: float) -> tuple[float, float]:
        return px - self.x, py - self.y

    def button_at(self, px: float, py: float) -> Button | None:
        lx, ly = self.local(px, py)
        for button in self.buttons:
            bx, by, bw, bh = button.rect
            if bx <= lx < bx + bw and by <= ly < by + bh:
                return button
        return None

    def in_title(self, px: float, py: float) -> bool:
        return self.contains(px, py) and py - self.y < TITLE_H


def default_layout(panels: dict, width: int, height: int) -> dict[str, tuple[float, float]]:
    """Panel id -> top-left as fractions of the screen: small panels down the left, the
    model picker and the how-to down the right, the middle left free for the hologram."""
    gap, top, side = 22, 70, max(24, int(width * 0.025))
    positions, y = {}, top
    for panel_id in ("clock", "system", "music"):
        positions[panel_id] = (side, y)
        y += panels[panel_id].h + gap
    right = width - side - max(panels["models"].w, panels["help"].w)
    positions["models"] = (right, top)
    positions["help"] = (right, top + panels["models"].h + gap)
    return {k: (x / width, y / height) for k, (x, y) in positions.items()}


class HoloScene:
    def __init__(self, width: int, height: int, apps: list[str], layout: dict | None = None) -> None:
        self.width, self.height = width, height
        self.stats: dict = {}
        self.notice = ""              # a message across the middle, e.g. "Starting the camera…"
        self.layout_changed = False
        self.pressed: tuple[str, str, float] | None = None   # panel, button, when - for the flash
        self.hover: tuple[str, str] | None = None
        self.mode = "IDLE"            # what the hands are doing to the hologram, for the readout
        self.holo = Hologram()
        self.particles = Particles()
        self._drag: tuple[str, float, float] | None = None
        self._turning: tuple[float, float, float] | None = None   # last x, y, t of a one-hand turn
        self._zoom: tuple[float, float, float, float] | None = None  # start distance, angle, scale, roll
        self._was_pinching = False
        self._palms_since: float | None = None
        self._fist_since: float | None = None
        self._last_t: float | None = None
        self._fingertip: tuple[float, float] | None = None
        self._cache: dict = {}
        # Effects
        self.sounds: list[str] = []   # sound effects due this frame; the window plays and clears them
        self.sound_on = True
        self.trails_on = True
        self.boot_t0: float | None = None
        self.flash = 0.0              # a white flash after a repulsor blast, fading
        self.charge = 0.0             # repulsor charge 0..1
        self.charge_at = (0.0, 0.0)
        self.shockwaves: list[tuple[float, float, float]] = []   # x, y, when it fired
        self.lightning = False
        self._still_since: float | None = None
        self._last_palm: tuple[float, float, float] | None = None
        self._blast_ready_at = 0.0
        self._trails = (deque(), deque())   # recent (x, y, t) of each index fingertip
        self._carry: tuple[float, float] | None = None   # grab point relative to the hologram's centre
        self._carry_last: tuple[float, float, float] | None = None
        self._carry_velocity = np.zeros(2, np.float32)
        self._last_swipe = -10.0
        self._render_t = 0.0
        self._rng = np.random.default_rng()
        self.panels = self._build_panels(apps)
        positions = default_layout(self.panels, width, height)
        positions.update({k: tuple(v) for k, v in (layout or {}).items() if k in self.panels})
        for panel in self.panels.values():
            fx, fy = positions.get(panel.id, (0.4, 0.4))
            panel.x, panel.y = self._clamp(fx * width, fy * height, panel)
        self.order = list(self.panels)   # drawing order; the last is on top
        self._half = (max(2, width // 2), max(2, height // 2))
        self._vignette = self._make_vignette()

    # ------------------------------------------------------------- layout
    def _build_panels(self, apps: list[str]) -> dict[str, Panel]:
        w = self.width
        side = int(min(340, max(250, w * 0.2)))
        panels = {
            "clock": Panel("clock", "TIME", 0, 0, side, 150),
            "system": Panel("system", "SYSTEM", 0, 0, side, 210),
            "music": Panel("music", "MEDIA", 0, 0, side, 150),
            "models": Panel("models", "HOLOGRAM", 0, 0, side, 262),
            "help": Panel("help", "HANDS", 0, 0, side, 318),
        }
        bw = (side - 2 * 14 - 2 * 8) // 3
        for i, (bid, text, action) in enumerate((("prev", "PREV", "prev_track"), ("play", "PLAY", "play_pause"),
                                                  ("next", "NEXT", "next_track"), ("vdown", "VOL −", "volume_down"),
                                                  ("mute", "MUTE", "mute"), ("vup", "VOL +", "volume_up"))):
            row, col = divmod(i, 3)
            panels["music"].buttons.append(Button(bid, text, (14 + col * (bw + 8), TITLE_H + 14 + row * 48, bw, 40),
                                                  "media:" + action))
        for i, name in enumerate(MODELS):
            row, col = divmod(i, 3)
            panels["models"].buttons.append(Button(name, name.upper(), (14 + col * (bw + 8), TITLE_H + 14 + row * 48,
                                                                        bw, 40), "model:" + name))
        panels["models"].buttons.append(Button("reset", "RESET", (14 + 2 * (bw + 8), TITLE_H + 14 + 48, bw, 40),
                                               "holo:reset", AMBER))
        wide = (side - 2 * 14 - 8) // 2
        for i, (bid, text) in enumerate((("sound", "SOUND"), ("trails", "TRAILS"))):
            panels["models"].buttons.append(Button(bid, text, (14 + i * (wide + 8), TITLE_H + 14 + 96, wide, 40),
                                                   "holo:" + bid, GREEN))
        entries = [(f"app{i}", name.upper()[:12], "app:" + name) for i, name in enumerate(apps[:4])]
        entries.append(("exit", "EXIT", "exit"))
        dock = Panel("dock", "", 0, 0, 24 + len(entries) * 150, 64)
        for i, (bid, text, action) in enumerate(entries):
            dock.buttons.append(Button(bid, text, (12 + i * 150, 12, 140, 40), action, RED if bid == "exit" else CYAN))
        panels["dock"] = dock
        return panels

    def _clamp(self, x: float, y: float, panel: Panel) -> tuple[float, float]:
        if panel.id == "dock":
            return (self.width - panel.w) / 2, self.height - panel.h - 18
        return (min(max(x, 0.0), self.width - panel.w), min(max(y, 0.0), self.height - panel.h - 90))

    def layout(self) -> dict[str, tuple[float, float]]:
        return {p.id: (round(p.x / self.width, 4), round(p.y / self.height, 4))
                for p in self.panels.values() if p.id != "dock"}

    def stage(self) -> tuple[float, float, float]:
        """Where the hologram floats: centre x, y and its size in px (radius at scale 1)."""
        left = self.panels["clock"].w + 60
        right = self.width - self.panels["models"].w - 60
        cx = (left + right) / 2 if right > left else self.width / 2
        cy = self.height * 0.47
        size = min(right - left, self.height * 0.8) * 0.3
        return cx, cy, max(size, 80.0)

    def center(self) -> tuple[float, float, float]:
        """Where the hologram is now: its home plus wherever it was carried or thrown."""
        cx, cy, size = self.stage()
        return cx + float(self.holo.offset[0]), cy + float(self.holo.offset[1]), size

    def _bounds(self) -> tuple[float, float, float, float]:
        cx, cy, size = self.stage()
        r = size * self.holo.scale * 0.75
        return r - cx, r - cy, self.width - r - cx, self.height - 90 - r - cy

    def _sound(self, name: str) -> None:
        if self.sound_on:
            self.sounds.append(name)

    def _top_panel_at(self, x: float, y: float) -> Panel | None:
        for panel_id in reversed(self.order):
            panel = self.panels[panel_id]
            if panel.contains(x, y):
                return panel
        return None

    # ------------------------------------------------------------- interaction
    def handle(self, hand: Hand, t: float, gesture: str = "") -> list[str]:
        """One frame of input. Returns the actions for the window to perform:
        "media:<action>", "app:<name>", "exit"."""
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 0.1))
        self._last_t = t
        actions: list[str] = []
        if gesture == "both_palms":
            if self._palms_since is None:
                self._palms_since = t
            if t - self._palms_since >= EXIT_HOLD:
                self._palms_since = None
                return ["exit"]
        else:
            self._palms_since = None

        # A fist bursts the hologram; opening the hand brings it back together.
        if gesture == "fist":
            if self._fist_since is None:
                self._fist_since = t
            if t - self._fist_since >= 0.15:
                self.holo.explode_target = 1.0
        else:
            self._fist_since = None
            self.holo.explode_target = 0.0
        if self.holo.explode_target == 1.0 and self.holo.explode < 0.05:
            self._sound("explode")

        pinching = hand.present and hand.pinching
        if gesture in ("swipe_left", "swipe_right") and not pinching and t - self._last_swipe > 0.8:
            self._last_swipe = t
            step = 1 if gesture == "swipe_left" else -1
            self.holo.set_model(MODELS[(MODELS.index(self.holo.model) + step) % len(MODELS)])
            self.holo.explode = 1.0   # the next model flies together
            self._sound("whoosh")
        self._repulsor(hand, t, gesture)
        self._trail(hand, t)
        lightning = (hand.present and hand.second is not None and not hand.pinching
                     and not hand.second.pinching and gesture != "fist")
        if lightning and not self.lightning:
            self._sound("zap")
        self.lightning = lightning
        started, ended = pinching and not self._was_pinching, self._was_pinching and not pinching
        self._was_pinching = pinching
        self.hover = None
        self._fingertip = hand.tip if hand.present else None
        two = hand.present and hand.second is not None and hand.pinching and hand.second.pinching

        if two and self._drag is None:
            self._two_hands(hand, hand.second)
        else:
            self._zoom = None
            if hand.present:
                panel = self._top_panel_at(hand.x, hand.y)
                button = panel.button_at(hand.x, hand.y) if panel else None
                if panel and button:
                    self.hover = (panel.id, button.id)
                if started:
                    if panel is not None:
                        self._raise(panel.id)
                        if button is not None:
                            self.pressed = (panel.id, button.id, t)
                            actions += self._press(button)
                        elif panel.in_title(hand.x, hand.y) and panel.id != "dock":
                            self._drag = (panel.id, hand.x - panel.x, hand.y - panel.y)
                    else:
                        cx, cy, size = self.center()
                        if math.hypot(hand.x - cx, hand.y - cy) < size * self.holo.scale * 0.55:
                            # On the hologram itself: pick it up (move it, or throw it).
                            self._carry = (cx - hand.x, cy - hand.y)
                            self._carry_last = (hand.x, hand.y, t)
                            self._carry_velocity[:] = 0
                            self.holo.carried, self.holo.velocity[:] = True, 0
                            self._sound("grab")
                        else:
                            self._turning = (hand.x, hand.y, t)
                            self.holo.grabbed = True
                elif pinching:
                    if self._drag is not None:
                        panel = self.panels[self._drag[0]]
                        panel.x, panel.y = self._clamp(hand.x - self._drag[1], hand.y - self._drag[2], panel)
                    elif self._carry is not None:
                        home_x, home_y, _size = self.stage()
                        self.holo.offset[:] = (hand.x + self._carry[0] - home_x, hand.y + self._carry[1] - home_y)
                        lx, ly, lt = self._carry_last
                        if t > lt:
                            measured = np.array([(hand.x - lx) / (t - lt), (hand.y - ly) / (t - lt)], np.float32)
                            self._carry_velocity = self._carry_velocity * 0.4 + measured * 0.6
                        self._carry_last = (hand.x, hand.y, t)
                    elif self._turning is not None:
                        lx, ly, lt = self._turning
                        self.holo.turn(hand.x - lx, hand.y - ly, t - lt)
                        self._turning = (hand.x, hand.y, t)
        if ended or not hand.present:
            if self._drag is not None:
                self._drag = None
                self.layout_changed = True
            self._turning = None
            self.holo.grabbed = False
            if self._carry is not None:
                self._carry = None
                self.holo.carried = False
                speed = float(np.linalg.norm(self._carry_velocity))
                self.holo.velocity = self._carry_velocity * min(1.0, 4000.0 / max(speed, 1.0))
                if speed > 300:
                    self._sound("throw")
        self._time_passes(dt)
        self.mode = ("CARRIED" if self._carry else "CHARGING" if self.charge > 0 else
                     "ZOOM" if self._zoom else "TURNING" if self._turning else
                     "EXPLODED" if self.holo.explode > 0.5 else "SPINNING" if abs(self.holo.spin[0]) > IDLE_SPIN * 1.5
                     else "IDLE")
        return actions

    def idle(self, t: float) -> None:
        """Time passes with no new hand input (between camera frames)."""
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 0.1))
        self._last_t = t
        self._time_passes(dt)

    def _time_passes(self, dt: float) -> None:
        self.holo.step(dt)
        if self.holo.fly(dt, self._bounds()):
            self._sound("bounce")
        self.flash = max(0.0, self.flash - dt * 2.5)

    def _repulsor(self, hand: Hand, t: float, gesture: str) -> None:
        """Iron Man's repulsor: one open palm held still charges it (a ring grows in the
        palm), and at full charge it fires - a shockwave, a flash, the particles blown
        away and the hologram knocked spinning. Moving the palm or closing it cancels."""
        palm_ok = hand.present and gesture == "open_palm" and hand.second is None and len(hand.skeleton) > 9
        if not palm_ok:
            if self.charge > 0:
                self._sound("charge_stop")   # cancelled: cut the rising hum
            self._still_since, self._last_palm, self.charge = None, None, 0.0
            return
        px, py = hand.skeleton[9]   # the middle of the palm
        if self._last_palm is not None:
            lx, ly, lt = self._last_palm
            speed = math.hypot(px - lx, py - ly) / max(t - lt, 1e-3)
            if speed > STILL_SPEED:
                if self.charge > 0:
                    self._sound("charge_stop")
                self._still_since, self.charge = None, 0.0
        self._last_palm = (px, py, t)
        if t < self._blast_ready_at:
            self.charge = 0.0
            return
        if self._still_since is None:
            self._still_since = t
        level = (t - self._still_since - CHARGE_START) / (CHARGE_FULL - CHARGE_START)
        if level > 0 and self.charge == 0:
            self._sound("charge")
        self.charge, self.charge_at = max(0.0, min(level, 1.0)), (px, py)
        if level >= 1.0:
            self._blast(px, py, t)

    def _blast(self, px: float, py: float, t: float) -> None:
        self.shockwaves.append((px, py, t))
        self.shockwaves = self.shockwaves[-4:]
        self.flash, self.charge = 1.0, 0.0
        self._still_since, self._blast_ready_at = None, t + BLAST_COOLDOWN
        cx, cy, size = self.center()
        direction = 1.0 if cx >= px else -1.0
        self.holo.spin[0] = direction * 7.5          # knocked spinning, away from the palm
        self.holo.spin[1] = (1.0 if cy >= py else -1.0) * 2.0
        positions = self.particles.positions(cx, cy, size, self.holo.scale)
        away = positions - np.array([px, py], np.float32)
        dist = np.maximum(np.linalg.norm(away, axis=1, keepdims=True), 1.0)
        self.particles.offset += away / dist * (420.0 * np.exp(-dist / 700.0))
        self._sound("blast")

    def _trail(self, hand: Hand, t: float) -> None:
        for trail, one in zip(self._trails, (hand, hand.second)):
            if self.trails_on and one is not None and one.present and not one.pinching:
                trail.append((one.tip[0], one.tip[1], t))
            while trail and t - trail[0][2] > TRAIL_SECONDS:
                trail.popleft()

    def _two_hands(self, a: Hand, b: Hand) -> None:
        distance = max(math.hypot(a.x - b.x, a.y - b.y), 1.0)
        angle = math.atan2(b.y - a.y, b.x - a.x)
        if self._zoom is None:
            self._zoom = (distance, angle, self.holo.scale, self.holo.roll)
            self._turning = None
            self.holo.grabbed = True
            return
        d0, a0, s0, r0 = self._zoom
        self.holo.scale = max(SCALE_RANGE[0], min(SCALE_RANGE[1], s0 * distance / d0))
        twist = (angle - a0 + math.pi) % (2 * math.pi) - math.pi
        self.holo.roll = r0 - twist

    def _press(self, button: Button) -> list[str]:
        kind, _, value = button.action.partition(":")
        if kind == "model":
            if value != self.holo.model:
                self.holo.set_model(value)
                self.holo.explode = 1.0   # it assembles itself
                self._sound("whoosh")
            return []
        if button.action == "holo:reset":
            self.holo.yaw, self.holo.pitch, self.holo.roll, self.holo.scale = 0.6, -0.35, 0.0, 1.0
            self.holo.spin[:] = (IDLE_SPIN, 0.0)
            self.holo.offset[:], self.holo.velocity[:] = 0, 0
            return []
        if button.action == "holo:sound":
            self.sound_on = not self.sound_on
            self._sound("click")
            return []
        if button.action == "holo:trails":
            self.trails_on = not self.trails_on
            return []
        self._sound("click")
        return [button.action]

    def _raise(self, panel_id: str) -> None:
        if panel_id != "dock":
            self.order.remove(panel_id)
            self.order.insert(len(self.order) - 1 if "dock" in self.order else len(self.order), panel_id)

    # ------------------------------------------------------------- rendering
    def _make_vignette(self) -> np.ndarray:
        w, h = self._half
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xs - w / 2) / (w / 2)) ** 2 + ((ys - h / 2) / (h / 2)) ** 2)
        return np.clip(0.42 - 0.18 * d, 0.12, 0.42)[..., None]

    def render(self, frame: np.ndarray | None, hand: Hand, t: float) -> np.ndarray:
        """The whole screen as an RGB array (height, width, 3)."""
        if self.boot_t0 is None:
            self.boot_t0 = t
            self._sound("boot")
        boot = min(1.0, (t - self.boot_t0) / BOOT_SECONDS)
        self._render_t = t
        self.shockwaves = [w for w in self.shockwaves if t - w[2] < 0.8]
        cx, cy, size = self.center()
        pieces = self.holo.project(cx, cy, size, t)
        sparks = self.holo.sparks(cx, cy, size, t)
        dots = self.particles.step(1 / 30, cx, cy, size, self.holo.scale, self._fingertip)
        bolts = self._bolts(hand) if self.lightning else []
        out = self._background(frame, hand, pieces, sparks, dots, bolts, t)
        self._grid(out)
        self._hologram_lines(out, pieces, sparks, dots)
        self._effect_lines(out, bolts, t)
        opacity = min(1.0, max(0.0, (boot - 0.25) / 0.5))   # panels fade in during the boot
        if opacity > 0:
            for panel_id in self.order:
                panel = self.panels[panel_id]
                _blend(out, self._panel_image(panel, t), int(panel.x) - GLOW, int(panel.y) - GLOW, opacity)
            self._header(out, opacity)
        if boot < 1.0:
            self._boot(out, boot)
        if self.notice and boot >= 1.0:
            self._notice(out)
        if hand.present:
            self._hand_lines(out, hand)
            self._pointer(out, hand)
            if hand.second is not None:
                self._hand_lines(out, hand.second)
                self._pointer(out, hand.second)
                if self._zoom is not None:
                    cv2.line(out, (int(hand.x), int(hand.y)), (int(hand.second.x), int(hand.second.y)),
                             AMBER, 2, cv2.LINE_AA)
        if self.flash > 0.01:
            lift = int(110 * self.flash)
            out = cv2.add(out, (lift, lift, lift, 0))
        return out

    def _bolts(self, hand: Hand) -> list[np.ndarray]:
        """Two fresh lightning bolts between the index fingertips (new every frame, so they
        crackle)."""
        a, b = np.array(hand.tip, np.float32), np.array(hand.second.tip, np.float32)
        return [_jagged(a, b, self._rng) for _ in range(2)]

    def _background(self, frame, hand: Hand, pieces, sparks, dots, bolts, t: float) -> np.ndarray:
        w, h = self._half
        if frame is None:
            base = np.zeros((h, w, 3), np.float32)
        else:
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            base = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32)
            gray = base.mean(axis=2, keepdims=True)
            base = 0.55 * gray + 0.45 * base     # partly drained of colour
        out = base * self._vignette + np.array([0, 16, 34], np.float32)
        glow = np.zeros((h, w, 3), np.uint8)
        hot = AMBER if self.holo.explode > 0.3 else CYAN
        for points, light in pieces:
            colour = tuple(int(c * light) for c in hot)
            cv2.polylines(glow, [points // 2], False, colour, 5, cv2.LINE_AA)
        for x, y in sparks:
            cv2.circle(glow, (x // 2, y // 2), 9, WHITE, -1, cv2.LINE_AA)
        for x, y in dots.astype(np.int32) // 2:
            cv2.circle(glow, (int(x), int(y)), 2, CYAN, -1)
        if hand.present:
            for one in (hand, hand.second):
                if one is None or not one.skeleton:
                    continue
                pts = [(int(x / 2), int(y / 2)) for x, y in one.skeleton]
                colour = AMBER if one.pinching else CYAN
                for a, b in BONES:
                    cv2.line(glow, pts[a], pts[b], colour, 6, cv2.LINE_AA)
        for trail in self._trails:   # comet tails: thick and bright at the head, thin and dim behind
            points = list(trail)
            for (x0, y0, t0), (x1, y1, _t1) in zip(points, points[1:]):
                k = max(0.0, 1.0 - (t - t0) / TRAIL_SECONDS)
                colour = (int(120 * k), int(214 * k), int(255 * k))
                cv2.line(glow, (int(x0 / 2), int(y0 / 2)), (int(x1 / 2), int(y1 / 2)), colour,
                         max(1, int(1 + 8 * k)), cv2.LINE_AA)
        for bolt in bolts:
            cv2.polylines(glow, [(bolt / 2).astype(np.int32)], False, (170, 210, 255), 7, cv2.LINE_AA)
        if self.charge > 0:
            px, py = self.charge_at
            radius = int((18 + 55 * self.charge) / 2)
            colour = tuple(int(a + (b - a) * self.charge) for a, b in zip(CYAN, WHITE))
            cv2.circle(glow, (int(px / 2), int(py / 2)), radius, colour, -1, cv2.LINE_AA)
        for x, y, t0 in self.shockwaves:
            age = (t - t0) / 0.8
            if 0 <= age < 1:
                k = 1.0 - age
                cv2.circle(glow, (int(x / 2), int(y / 2)), int(age * 900), (int(140 * k), int(230 * k), int(255 * k)),
                           max(2, int(18 * k)), cv2.LINE_AA)
        glow = cv2.GaussianBlur(glow, (0, 0), 6)
        out += glow.astype(np.float32) * 1.1
        out = np.clip(out, 0, 255).astype(np.uint8)
        return cv2.resize(out, (self.width, self.height), interpolation=cv2.INTER_LINEAR)

    def _effect_lines(self, out: np.ndarray, bolts, t: float) -> None:
        """The sharp cores over the glows: lightning, trail heads, the charge ring, shockwaves."""
        for bolt in bolts:
            cv2.polylines(out, [bolt.astype(np.int32)], False, WHITE, 2, cv2.LINE_AA)
        for trail in self._trails:
            points = [(int(x), int(y)) for x, y, t0 in trail if t - t0 < TRAIL_SECONDS * 0.4]
            if len(points) > 1:
                cv2.polylines(out, [np.array(points, np.int32)], False, WHITE, 2, cv2.LINE_AA)
        if self.charge > 0:
            px, py = (int(v) for v in self.charge_at)
            radius = int(18 + 55 * self.charge)
            cv2.circle(out, (px, py), radius, WHITE, 2, cv2.LINE_AA)
            for i in range(8):   # a turning ring of ticks, like an arc reactor spinning up
                a = t * 6.0 + i * math.pi / 4
                inner, outer = radius + 6, radius + 6 + int(10 * self.charge)
                cv2.line(out, (int(px + inner * math.cos(a)), int(py + inner * math.sin(a))),
                         (int(px + outer * math.cos(a)), int(py + outer * math.sin(a))), CYAN, 2, cv2.LINE_AA)
        for x, y, t0 in self.shockwaves:
            age = (t - t0) / 0.8
            if 0 <= age < 1:
                cv2.circle(out, (int(x), int(y)), int(age * 1800), WHITE, 2, cv2.LINE_AA)

    def _boot(self, out: np.ndarray, boot: float) -> None:
        """The start-up: a scan ring sweeping out from the middle and the title."""
        cx, cy, _size = self.stage()
        radius = int(boot * max(self.width, self.height) * 0.9)
        k = 1.0 - boot
        cv2.circle(out, (int(cx), int(cy)), radius, tuple(int(c * k) for c in CYAN), 3, cv2.LINE_AA)
        cv2.circle(out, (int(cx), int(cy)), int(radius * 0.8), tuple(int(c * k * 0.5) for c in CYAN), 1, cv2.LINE_AA)
        key = ("boot", self.width)
        if key not in self._cache:
            big, small = font(54, "head"), font(20, "head")
            title, line = "TOUCHLESSX  HOLO", "SYSTEMS ONLINE"
            width = max(big.getbbox(title)[2], small.getbbox(line)[2]) + 40
            image = Image.new("RGBA", (width, 110), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.text(((width - big.getbbox(title)[2]) / 2, 0), title, font=big, fill=WHITE + (255,))
            draw.text(((width - small.getbbox(line)[2]) / 2, 72), line, font=small, fill=CYAN + (255,))
            self._cache[key] = image
        image = self._cache[key]
        opacity = min(1.0, boot / 0.2, (1.0 - boot) / 0.25)
        _blend(out, image, int(cx - image.width / 2), int(cy - image.height / 2), max(0.0, opacity))

    def _hologram_lines(self, out: np.ndarray, pieces, sparks, dots) -> None:
        hot = AMBER if self.holo.explode > 0.3 else CYAN
        for points, light in pieces:
            colour = tuple(min(255, int((c * 0.8 + 50) * light)) for c in hot)   # front bright, back faint
            cv2.polylines(out, [points], False, colour, 1, cv2.LINE_AA)
        for x, y in sparks:
            cv2.circle(out, (x, y), 4, WHITE, -1, cv2.LINE_AA)
        for x, y in dots.astype(np.int32):
            out[max(0, min(int(y), self.height - 1)), max(0, min(int(x), self.width - 1))] = WHITE

    def _grid(self, out: np.ndarray) -> None:
        step = 64
        out[::step, :, :] = (out[::step, :, :] * 0.7 + np.array([0, 60, 90]) * 0.3).astype(np.uint8)
        out[:, ::step, :] = (out[:, ::step, :] * 0.7 + np.array([0, 60, 90]) * 0.3).astype(np.uint8)

    def _header(self, out: np.ndarray, opacity: float = 1.0) -> None:
        key = ("header", self.width)
        if key not in self._cache:
            text = "PINCH SPACE + MOVE = TURN   |   TWO HANDS = ZOOM   |   FIST = EXPLODE   |   BOTH PALMS OR ESC = EXIT"
            f = font(17, "head")
            box = f.getbbox(text)
            image = Image.new("RGBA", (box[2] + 40, 40), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((0, 0, image.width - 1, 39), 20, fill=(4, 16, 32, 150), outline=CYAN + (120,))
            draw.text((20, 10), text, font=f, fill=CYAN + (230,))
            self._cache[key] = image
        image = self._cache[key]
        _blend(out, image, (self.width - image.width) // 2, 14, opacity)

    def _notice(self, out: np.ndarray) -> None:
        key = ("notice", self.notice)
        if key not in self._cache:
            f = font(26, "head")
            box = f.getbbox(self.notice)
            image = Image.new("RGBA", (box[2] + 60, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((0, 0, image.width - 1, 63), 18, fill=(30, 18, 4, 200), outline=AMBER + (220,), width=2)
            draw.text((30, 16), self.notice, font=f, fill=AMBER + (255,))
            self._cache = {k: v for k, v in self._cache.items() if not (isinstance(k, tuple) and k[0] == "notice")}
            self._cache[key] = image
        image = self._cache[key]
        _blend(out, image, (self.width - image.width) // 2, self.height - 170)

    def _hand_lines(self, out: np.ndarray, hand: Hand) -> None:
        colour = AMBER if hand.pinching else CYAN
        pts = [(int(x), int(y)) for x, y in hand.skeleton]
        for a, b in BONES:
            cv2.line(out, pts[a], pts[b], colour, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(out, p, 3, WHITE, -1, cv2.LINE_AA)

    def _pointer(self, out: np.ndarray, hand: Hand) -> None:
        centre = (int(hand.x), int(hand.y))
        colour = AMBER if hand.pinching else CYAN
        cv2.circle(out, centre, 16, colour, 2, cv2.LINE_AA)
        cv2.circle(out, centre, 4 if not hand.pinching else 9, colour, -1, cv2.LINE_AA)

    # ------------------------------------------------------------- panels
    def _content_key(self, panel: Panel, t: float):
        hover = self.hover[1] if self.hover and self.hover[0] == panel.id else None
        flash = self.pressed[1] if self.pressed and self.pressed[0] == panel.id and t - self.pressed[2] < 0.25 else None
        if panel.id == "clock":
            return (time.strftime("%H:%M:%S"), time.strftime("%A %d %B"))
        if panel.id == "system":
            return tuple(sorted((k, str(v)) for k, v in self.stats.items()))
        if panel.id == "models":
            return (hover, flash, self.holo.model, self.mode, round(self.holo.scale, 2), self.sound_on, self.trails_on,
                    round(float(abs(self.holo.spin[0])), 1))
        return (hover, flash)

    def _panel_image(self, panel: Panel, t: float) -> Image.Image:
        key = self._content_key(panel, t)
        cached = self._cache.get(panel.id)
        if cached and cached[0] == key:
            return cached[1]
        image = self._draw_panel(panel, key)
        self._cache[panel.id] = (key, image)
        return image

    def _draw_panel(self, panel: Panel, key) -> Image.Image:
        pad = GLOW   # room for the glow around the frame
        w, h = panel.w + 2 * pad, panel.h + 2 * pad
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((pad, pad, pad + panel.w - 1, pad + panel.h - 1), 16,
                                               outline=CYAN + (170,), width=4)
        image = glow.filter(ImageFilter.GaussianBlur(5))
        body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(body)
        draw.rounded_rectangle((pad, pad, pad + panel.w - 1, pad + panel.h - 1), 16, fill=PANEL_FILL,
                               outline=CYAN + (200,), width=1)
        if panel.title:
            draw.text((pad + 16, pad + 9), panel.title, font=font(15, "head"), fill=CYAN + (235,))
            draw.line((pad + 14, pad + TITLE_H, pad + panel.w - 14, pad + TITLE_H), fill=CYAN + (70,), width=1)
            for i in range(3):  # the grab handle
                x = pad + panel.w - 30 + i * 7
                draw.ellipse((x, pad + 15, x + 3, pad + 18), fill=CYAN + (150,))
        getattr(self, f"_fill_{panel.id}", lambda *_: None)(draw, panel, pad)
        hover = key[0] if panel.id not in ("clock", "system") else None
        flash = key[1] if panel.id not in ("clock", "system") else None
        for button in panel.buttons:
            selected = panel.id == "models" and (button.id == self.holo.model or (button.id == "sound" and self.sound_on)
                                                 or (button.id == "trails" and self.trails_on))
            self._button(draw, button, pad, button.id == hover, button.id == flash or selected)
        image.alpha_composite(body)
        return image

    @staticmethod
    def _button(draw: ImageDraw.ImageDraw, button: Button, pad: int, hover: bool, lit: bool) -> None:
        x, y, w, h = button.rect
        x, y = x + pad, y + pad
        accent = button.accent
        fill = accent + (210,) if lit else accent + (70,) if hover else (10, 40, 70, 160)
        draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), 10, fill=fill,
                               outline=accent + (255 if hover else 150,), width=2 if hover else 1)
        f = font(14, "head")
        box = f.getbbox(button.text)
        colour = (4, 16, 32, 255) if lit else WHITE + (240,)
        draw.text((x + (w - box[2]) / 2, y + (h - box[3]) / 2 - 1), button.text, font=f, fill=colour)

    def _fill_clock(self, draw, panel: Panel, pad: int) -> None:
        clock, date = time.strftime("%H:%M"), time.strftime("%A %d %B")
        f = font(64, "head")
        draw.text((pad + 16, pad + TITLE_H + 8), clock, font=f, fill=WHITE + (255,))
        width = f.getbbox(clock)[2]
        draw.text((pad + 22 + width, pad + TITLE_H + 34), time.strftime(":%S"), font=font(28, "head"), fill=CYAN + (220,))
        draw.text((pad + 18, pad + panel.h - 34), date.upper(), font=font(14, "head"), fill=DIM + (255,))

    def _fill_system(self, draw, panel: Panel, pad: int) -> None:
        rows = [("CPU", self.stats.get("cpu")), ("MEMORY", self.stats.get("memory"))]
        battery = self.stats.get("battery")
        rows.append(("BATTERY", battery[0] if battery else None))
        for i, (name, value) in enumerate(rows):
            y = pad + TITLE_H + 16 + i * 54
            draw.text((pad + 16, y), name, font=font(13, "head"), fill=DIM + (255,))
            shown = "--" if value is None else f"{value:.0f}%"
            f = font(20, "head")
            draw.text((pad + panel.w - 16 - f.getbbox(shown)[2], y - 4), shown, font=f, fill=WHITE + (255,))
            x0, x1, yb = pad + 16, pad + panel.w - 16, y + 24
            draw.rounded_rectangle((x0, yb, x1, yb + 8), 4, fill=(20, 60, 90, 200))
            if value is not None:
                colour = RED if (name != "BATTERY" and value > 85) or (name == "BATTERY" and value < 20) else CYAN
                draw.rounded_rectangle((x0, yb, x0 + (x1 - x0) * min(value, 100) / 100, yb + 8), 4, fill=colour + (255,))

    def _fill_models(self, draw, panel: Panel, pad: int) -> None:
        y = pad + TITLE_H + 14 + 3 * 48 + 6
        draw.line((pad + 14, y, pad + panel.w - 14, y), fill=CYAN + (50,), width=1)
        readout = [("MODE", self.mode), ("SCALE", f"{self.holo.scale * 100:.0f}%"),
                   ("SPIN", f"{abs(float(self.holo.spin[0])):.1f} RAD/S")]
        for i, (name, value) in enumerate(readout):
            x = pad + 16 + i * (panel.w - 32) / 3
            draw.text((x, y + 8), name, font=font(11, "head"), fill=DIM + (255,))
            colour = AMBER if name == "MODE" and value != "IDLE" else WHITE
            draw.text((x, y + 24), value, font=font(15, "head"), fill=colour + (255,))

    def _fill_help(self, draw, panel: Panel, pad: int) -> None:
        lines = [("PINCH SPACE + MOVE", "turn it, flick to spin"),
                 ("PINCH THE HOLOGRAM", "carry it - let go fast to throw"),
                 ("PINCH WITH BOTH HANDS", "pull apart to zoom, twist to tilt"),
                 ("MAKE A FIST", "explode it - open to rebuild"),
                 ("HOLD ONE PALM STILL", "charge the repulsor... blast"),
                 ("BOTH HANDS OPEN", "lightning between your fingers"),
                 ("SWIPE LEFT / RIGHT", "next model"),
                 ("BOTH PALMS OR ESC", "leave Holo mode")]
        for i, (head, text) in enumerate(lines):
            y = pad + TITLE_H + 10 + i * 34
            draw.text((pad + 16, y), head, font=font(13, "head"), fill=CYAN + (255,))
            draw.text((pad + 16, y + 17), text, font=font(13), fill=WHITE + (220,))


def _jagged(a: np.ndarray, b: np.ndarray, rng: np.random.Generator, depth: int = 6) -> np.ndarray:
    """A lightning bolt from a to b: midpoint displacement, each level half as wild."""
    points = [a, b]
    spread = float(np.linalg.norm(b - a)) * 0.2
    for _ in range(depth):
        nxt = [points[0]]
        for p, q in zip(points, points[1:]):
            mid = (p + q) / 2
            d = q - p
            normal = np.array([-d[1], d[0]], np.float32) / max(float(np.linalg.norm(d)), 1e-3)
            nxt += [mid + normal * rng.uniform(-spread, spread), q]
        points, spread = nxt, spread / 2
    return np.array(points, np.float32)


def _blend(out: np.ndarray, image: Image.Image, x: int, y: int, opacity: float = 1.0) -> None:
    """Alpha-blends an RGBA image onto the RGB frame with its top-left at (x, y), clipped
    to the frame; `opacity` fades the whole image."""
    h, w = out.shape[:2]
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + image.width, w), min(y + image.height, h)
    if right <= left or bottom <= top:
        return
    src = np.asarray(image)[top - y:bottom - y, left - x:right - x]
    alpha = src[..., 3:4].astype(np.uint16)
    if opacity < 1.0:
        alpha = (alpha * opacity).astype(np.uint16)
    region = out[top:bottom, left:right]
    region[...] = ((src[..., :3].astype(np.uint16) * alpha + region.astype(np.uint16) * (255 - alpha)) // 255
                   ).astype(np.uint8)

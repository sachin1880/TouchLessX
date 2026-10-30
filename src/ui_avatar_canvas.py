"""AI agent avatar — holographic circle renderer (20 fps).

Visual design
─────────────
  • Central glowing filled circle (flat, not a 3D sphere)
  • 3 concentric rings at different radii / brightness
  • State-specific animations:
      idle      – slow breathing pulse on all rings
      listening – 3 expanding ripple rings that grow & fade outward
      thinking  – 2 arcs rotating in opposite directions + dashes
      speaking  – 36 audio bars radiating from outer ring, animated
                  with multi-frequency sine waves (realistic voice look)
      executing – progress arc drawn around outer ring
      success   – burst of ripple rings + bright flash
      error     – red tint + horizontal jitter
      coding    – fast green rotating dashes (matrix feel)
      research  – blue scanning arc
      meeting   – blue ring with tick marks
      phone     – green bars (call waveform)
      focus     – dim static rings, no animation

Shared-canvas mode
──────────────────
  Pass ext_canvas + ext_img_id; the rendered PIL frame is pushed to a
  canvas image item on the caller's single tk.Canvas.  Required for macOS
  fullscreen Spaces (only the first Canvas renders there).
"""
from __future__ import annotations

import math
import random
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

from .agent_theme import get as _theme

_BG      = (3, 7, 15)
_BG_HEX  = "#03070f"
_TICK_MS = 40   # 25 fps  (smoother than before)

# Particle ring: N dots orbiting the circle
_N_PARTICLES = 14


def _cl(v: float) -> int:
    return max(0, min(255, int(v)))


# ── audio bar simulation (3 layered sine waves per bar) ───────────────────────
_FREQS = [
    (1.00, 1.0,  0.00),   # (speed, amplitude-weight, phase-offset)
    (1.73, 0.60, 1.10),
    (2.61, 0.35, 2.30),
]

def _bar_amp(i: int, n: int, t: float, scale: float) -> float:
    """Amplitude [0-1] for bar i at time t."""
    base_phase = (i / n) * math.tau
    v = 0.0
    for spd, w, ph in _FREQS:
        v += w * math.sin(t * spd + base_phase + ph)
    # normalise to [0, 1] and clamp
    return max(0.04, min(1.0, (v / 2.0 + 0.55) * scale))


class AvatarCanvas:
    """Call start() to begin the animation loop."""

    def __init__(self, parent: tk.Widget,
                 width: int = 480, height: int = 620,
                 ext_canvas=None, ext_img_id=None) -> None:
        self._w, self._h = width, height
        self._ext_canvas = ext_canvas
        self._ext_img_id = ext_img_id

        _cw = width  if ext_canvas is None else 1
        _ch = height if ext_canvas is None else 1
        self.canvas = tk.Canvas(parent, width=_cw, height=_ch,
                                bg=_BG_HEX, highlightthickness=0, bd=0)

        self._state    = "idle"
        self._angle    = 0.0    # degrees, incremented by theme speed
        self._pulse    = 0.0    # 0-1 breathing
        self._wave_t   = 0.0    # time for audio bars (radians)
        self._t        = 0      # frame counter

        self._progress  = 0.0
        self._err_jit   = 0.0
        self._ripples: list[dict] = []

        self._running  = False
        self._after_id = None

        # Pre-allocated RGBA buffer (avoids alloc every frame)
        self._buf   = Image.new("RGBA", (width, height), (*_BG, 255))
        self._photo = None
        self._img_id: int | None = None

    # ─────────────────────────────────────────────────────── public API
    def pack(self, **kw) -> None:  self.canvas.pack(**kw)
    def place(self, **kw) -> None: self.canvas.place(**kw)

    def set_state(self, state: str) -> None:
        prev = self._state
        self._state = state
        if state == "success" and prev != "success":
            self._ripples = [
                {"r": 0.0, "life": 1.0 - i * 0.15} for i in range(5)
            ]

    def set_progress(self, p: float) -> None:
        self._progress = max(0.0, min(1.0, p))

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id:
            _t = self._ext_canvas if self._ext_canvas is not None else self.canvas
            try:
                _t.after_cancel(self._after_id)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────── loop
    def _tick(self) -> None:
        if not self._running:
            return
        self._t += 1
        th = _theme(self._state)
        self._angle  = (self._angle + th.speed) % 360.0
        self._pulse  = (math.sin(math.radians(self._t * 2.2)) + 1) / 2
        self._wave_t += 0.09   # audio bar time (rad/frame)

        if self._state == "error":
            self._err_jit += 0.35
        else:
            self._err_jit = 0.0

        if self._state == "success":
            for rp in self._ripples:
                rp["r"]    += 0.025
                rp["life"] -= 0.022
            self._ripples = [r for r in self._ripples if r["life"] > 0]
            if len(self._ripples) < 3 and random.random() < 0.18:
                self._ripples.append({"r": 0.0, "life": 1.0})

        self._draw()
        _timer = self._ext_canvas if self._ext_canvas is not None else self.canvas
        self._after_id = _timer.after(_TICK_MS, self._tick)

    # ─────────────────────────────────────────────────────── render
    def _draw(self) -> None:
        W, H = self._w, self._h
        th = _theme(self._state)
        rc, gc, bc  = th.ring_rgb
        rb, gb, bbl = th.ring_bright_rgb

        bm = th.brightness_base + th.brightness_range * self._pulse

        # Error jitter
        jx = math.sin(self._err_jit * 3.7) * 6 if self._state == "error" else 0.0
        jy = math.sin(self._err_jit * 2.1) * 2 if self._state == "error" else 0.0
        cx = W / 2 + jx
        cy = H * 0.42 + jy   # slightly above centre (leave room for text below)

        R = int(min(W, H) * 0.26)   # core circle radius — larger for impact

        # Clear buffer
        self._buf.paste((*_BG, 255), [0, 0, W, H])
        img = self._buf
        d   = ImageDraw.Draw(img)

        # ── Ambient glow (large soft halos) — more layers, bigger ────────────
        for frac, a in [(5.0, 3), (4.0, 6), (3.0, 12), (2.2, 22), (1.7, 36)]:
            gr = int(R * frac)
            d.ellipse([cx-gr, cy-gr, cx+gr, cy+gr],
                      fill=(rc, gc, bc, _cl(a * bm)))

        # ── STATE-SPECIFIC RING OVERLAYS (drawn before core) ─────────────────
        self._draw_state_rings(d, cx, cy, R, rc, gc, bc, rb, gb, bbl, bm)

        # ── Core circle — 6 concentric fills (centre → edge) ─────────────────
        layers = [
            (1.00, 18), (0.84, 38), (0.66, 75),
            (0.46, 130), (0.26, 200), (0.10, 255),
        ]
        for frac, base_a in layers:
            r = int(R * frac)
            d.ellipse([cx-r, cy-r, cx+r, cy+r],
                      fill=(rc, gc, bc, _cl(base_a * bm)))

        # ── Rim / edge ring ───────────────────────────────────────────────────
        for w_, a_ in [(6, 55), (2, 120)]:
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(rb, gb, bbl, _cl(a_ * bm)), width=w_)

        # ── Inner ring (half-radius) ──────────────────────────────────────────
        r2 = int(R * 0.55)
        d.ellipse([cx-r2, cy-r2, cx+r2, cy+r2],
                  outline=(rb, gb, bbl, _cl(60 * bm)), width=2)

        # ── Specular highlight (bright arc at top-left of circle) ────────────
        sr = int(R * 0.88)
        d.arc([cx - sr, cy - sr, cx + sr, cy + sr],
              start=200, end=260, fill=(255, 255, 255, _cl(90 * bm)), width=3)
        ho = int(R * 0.30); hr = int(R * 0.14)
        d.ellipse([cx-ho-hr, cy-ho-hr, cx-ho+hr, cy-ho+hr],
                  fill=(255, 255, 255, _cl(70 * bm)))

        # ── Particle ring — small glowing dots orbiting the circle ───────────
        part_r = int(R * 1.30)
        pr_dot = max(3, int(R * 0.045))
        for k in range(_N_PARTICLES):
            ang    = math.radians(self._angle * 0.7 + k * (360 / _N_PARTICLES))
            bob    = math.sin(self._angle * 0.03 + k * 0.45) * int(R * 0.08)
            pr_off = int(part_r + bob)
            px     = int(cx + pr_off * math.cos(ang))
            py     = int(cy + pr_off * math.sin(ang))
            bright = 0.4 + 0.6 * ((math.cos(ang) + 1) / 2)
            pa     = _cl(200 * bm * bright)
            d.ellipse([px - pr_dot, py - pr_dot, px + pr_dot, py + pr_dot],
                      fill=(rb, gb, bbl, pa))

        # ── Three larger tick marks on outer rim ──────────────────────────────
        tick_r_in  = int(R * 1.50)
        tick_r_out = int(R * 1.64)
        for deg_off in [0, 120, 240]:
            ang = math.radians(self._angle + deg_off)
            x1  = int(cx + tick_r_in  * math.cos(ang))
            y1  = int(cy + tick_r_in  * math.sin(ang))
            x2  = int(cx + tick_r_out * math.cos(ang))
            y2  = int(cy + tick_r_out * math.sin(ang))
            d.line([x1, y1, x2, y2], fill=(rb, gb, bbl, _cl(160 * bm)), width=2)

        # ── Push frame ────────────────────────────────────────────────────────
        self._photo = ImageTk.PhotoImage(img.convert("RGB"))
        if self._ext_canvas is not None:
            self._ext_canvas.itemconfig(self._ext_img_id, image=self._photo)
        else:
            if self._img_id is None:
                self._img_id = self.canvas.create_image(0, 0, anchor="nw",
                                                         image=self._photo)
            else:
                self.canvas.itemconfig(self._img_id, image=self._photo)

    # ─────────────────────────────────────────────────────── state rings
    def _draw_state_rings(self, d, cx, cy, R,
                          rc, gc, bc, rb, gb, bbl, bm) -> None:
        s = self._state

        # ── IDLE — gentle outer ring only ────────────────────────────────────
        if s == "idle":
            ro = int(R * 1.35)
            d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                      outline=(rc, gc, bc, _cl(55 * bm)), width=2)

        # ── LISTENING — 5 expanding pulse rings (iOS Siri style) ─────────────
        elif s == "listening":
            for k, (frac, opa, wid) in enumerate([
                (1.25, 180, 3), (1.55, 120, 2),
                (1.90, 80,  2), (2.30, 48,  1), (2.80, 24, 1)
            ]):
                phase = (self._t * 0.020 + k * 0.44) % 1.0
                pr    = int(R * (frac + 0.35 * phase))
                pa    = _cl(opa * (1 - phase) * bm)
                d.ellipse([cx-pr, cy-pr, cx+pr, cy+pr],
                          outline=(rc, gc, bc, pa), width=wid)

        # ── SPEAKING  — audio bars radiating from outer ring ─────────────────
        elif s in ("speaking", "phone"):
            N          = 56                    # more bars → denser look
            bar_r_base = int(R * 1.05)
            bar_max    = int(R * 0.95)         # taller bars
            bar_w      = max(2, int(R * 0.025))
            for i in range(N):
                ang  = math.tau * i / N
                amp  = _bar_amp(i, N, self._wave_t, 1.0)
                blen = int(bar_max * amp)
                r0   = bar_r_base
                r1   = bar_r_base + blen
                x0   = int(cx + r0 * math.cos(ang))
                y0   = int(cy + r0 * math.sin(ang))
                x1   = int(cx + r1 * math.cos(ang))
                y1   = int(cy + r1 * math.sin(ang))
                alpha = _cl((100 + 155 * amp) * bm)
                d.line([x0, y0, x1, y1],
                       fill=(rb, gb, bbl, alpha), width=bar_w)
            # two glow rings for depth
            for rscale, rw, ra in [(1.05, 5, 60), (1.05, 2, 140)]:
                ro = int(R * rscale)
                d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                          outline=(rb, gb, bbl, _cl(ra * bm)), width=rw)

        # ── THINKING / RESEARCH — counter-rotating arcs ───────────────────────
        elif s in ("thinking", "research", "coding"):
            ar1 = int(R * 1.42)
            ar2 = int(R * 1.65)
            ar3 = int(R * 1.88)
            aa  = _cl((0.6 + 0.35 * self._pulse) * 200)

            # arc 1: 200° forward
            start1 = int(self._angle * 1.2) % 360
            d.arc([cx-ar1, cy-ar1, cx+ar1, cy+ar1],
                  start=start1, end=(start1 + 200) % 360,
                  fill=(rb, gb, bbl, aa), width=3)

            # arc 2: 130° backward
            start2 = int(-self._angle * 1.9) % 360
            d.arc([cx-ar2, cy-ar2, cx+ar2, cy+ar2],
                  start=start2, end=(start2 + 130) % 360,
                  fill=(rc, gc, bc, _cl(aa * 0.55)), width=2)

            # dashes on third ring
            for k in range(6):
                a0 = int(self._angle * 0.7 + k * 60) % 360
                d.arc([cx-ar3, cy-ar3, cx+ar3, cy+ar3],
                      start=a0, end=(a0 + 18) % 360,
                      fill=(rc, gc, bc, _cl(aa * 0.35)), width=1)

        # ── EXECUTING — progress arc ──────────────────────────────────────────
        elif s == "executing":
            ar = int(R * 1.50)
            sweep = int(360 * self._progress)
            if sweep > 0:
                d.arc([cx-ar, cy-ar, cx+ar, cy+ar],
                      start=270, end=(270 + sweep) % 360,
                      fill=(rb, gb, bbl, _cl(220 * bm)), width=4)
            # trailing dots
            d.arc([cx-ar, cy-ar, cx+ar, cy+ar],
                  start=270, end=360,
                  fill=(rc, gc, bc, _cl(55 * bm)), width=2)

        # ── SUCCESS — ripple bursts ───────────────────────────────────────────
        elif s == "success":
            for rp in self._ripples:
                lf = rp["life"]
                ri = int(R * (1.1 + rp["r"] * 2.5))
                d.ellipse([cx-ri, cy-ri, cx+ri, cy+ri],
                          outline=(rb, gb, bbl, _cl(lf * 180)), width=3)
            # bright static ring
            ro = int(R * 1.30)
            d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                      outline=(rb, gb, bbl, _cl(140 * bm)), width=2)

        # ── ERROR — red ring + no outer ring (handled by red colour theme) ────
        elif s == "error":
            ro = int(R * 1.40)
            d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                      outline=(rb, gb, bbl, _cl(160 * bm)), width=3)
            # fast blinking inner ring
            if self._t % 8 < 4:
                d.ellipse([cx-R, cy-R, cx+R, cy+R],
                          outline=(rb, gb, bbl, 200), width=4)

        # ── MEETING — clock tick marks ────────────────────────────────────────
        elif s == "meeting":
            ro = int(R * 1.45)
            d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                      outline=(rc, gc, bc, _cl(90 * bm)), width=2)
            for k in range(12):
                ang = math.radians(k * 30)
                ri  = ro - (8 if k % 3 == 0 else 4)
                d.line([int(cx + ri * math.cos(ang)),
                        int(cy + ri * math.sin(ang)),
                        int(cx + ro * math.cos(ang)),
                        int(cy + ro * math.sin(ang))],
                       fill=(rb, gb, bbl, _cl(140 * bm)), width=1)

        # ── FOCUS — very dim static rings only ───────────────────────────────
        elif s == "focus":
            for frac, a in [(1.30, 30), (1.60, 18)]:
                ro = int(R * frac)
                d.ellipse([cx-ro, cy-ro, cx+ro, cy+ro],
                          outline=(rc, gc, bc, a), width=1)

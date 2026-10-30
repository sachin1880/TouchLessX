"""Siri-style wake activation overlay — animated gradient wave bar.

A narrow, frameless, always-on-top strip anchored to the bottom-center
of the primary screen.  Appears when the wake word fires; disappears
after listening ends.

Visual design (inspired by iOS 16 Siri):
  • Frosted dark pill background
  • 4-color animated gradient wave (teal → blue → violet → pink)
  • 3 overlapping sine-wave paths composited with alpha
  • "Hey Computer…" label fades in then out
  • Transitions: fade in → listen animation → fade out
"""
from __future__ import annotations

import math
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

# ── constants ─────────────────────────────────────────────────────────────────
_W         = 520          # bar width
_H         = 96           # bar height
_CORNER_R  = _H // 2      # pill shape
_TICK_MS   = 40           # ~25 fps
_FADE_STEPS = 10          # frames for fade in / out

# Siri gradient palette (RGBA)
_COLORS = [
    (0,   212, 255, 255),   # teal
    (80,  160, 255, 255),   # sky blue
    (138, 100, 240, 255),   # violet
    (255,  60, 160, 255),   # pink
    (255, 140,  40, 200),   # amber accent
]

# Three sine-wave layers — (x-speed, y-speed, amplitude, color-index)
_WAVES = [
    (1.60, 1.10, 18, 0),   # teal foreground
    (1.00, 0.85, 22, 2),   # violet mid
    (2.10, 1.40, 14, 3),   # pink back
]

_BG   = (8, 12, 24, 220)   # dark navy, semi-transparent


def _lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))


def _make_frame(t: float, alpha: float, W: int, H: int) -> Image.Image:
    """Render one animation frame at global time *t* and overall *alpha* [0-1]."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── pill background ────────────────────────────────────────────────────────
    r = _CORNER_R
    bg = (*_BG[:3], int(_BG[3] * alpha))
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=bg)

    cx = W // 2
    cy = H // 2

    # ── draw each wave layer ───────────────────────────────────────────────────
    bar_count = 64
    max_amp   = H * 0.32

    for xspd, yspd, amp_scale, ci in _WAVES:
        c1 = _COLORS[ci]
        c2 = _COLORS[(ci + 1) % len(_COLORS)]

        points = []
        for i in range(bar_count + 1):
            px = int((i / bar_count) * W)
            phase = (i / bar_count) * math.tau
            y_off = (
                math.sin(t * xspd + phase) * amp_scale
                + math.sin(t * yspd + phase * 1.7) * (amp_scale * 0.5)
            )
            points.append((px, cy + y_off))

        # mirror points to close the polygon
        top_pts = [(x, y - 2) for x, y in points]
        bot_pts = [(x, y + 2) for x, y in reversed(points)]
        poly = top_pts + bot_pts

        blend = (math.sin(t * 0.6 + ci) + 1) / 2
        col = _lerp_color(c1, c2, blend)
        col = (*col[:3], int(col[3] * alpha * 0.75))
        draw.polygon(poly, fill=col)

    # ── subtle centre glow dot ─────────────────────────────────────────────────
    gr = int(12 * alpha)
    if gr > 0:
        glow_col = (*_COLORS[0][:3], int(140 * alpha))
        draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=glow_col)

    return img


class SiriWave:
    """Frameless floating wave overlay.

    Usage::
        wave = SiriWave(root)
        wave.show()     # on wake word
        wave.hide()     # when listening ends
    """

    def __init__(self, root: tk.Misc) -> None:
        self._root   = root
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._tk_img: ImageTk.PhotoImage | None = None
        self._t      = 0.0
        self._alpha  = 0.0           # 0 = fully transparent, 1 = fully visible
        self._target = 0.0           # 1 = show, 0 = hide
        self._after_id: str | None = None
        self._build()

    # ── public ────────────────────────────────────────────────────────────────

    def show(self) -> None:
        """Slide in and start animating."""
        if self._win:
            self._win.deiconify()
            self._win.lift()
            self._win.attributes("-topmost", True)
        self._target = 1.0
        self._ensure_loop()

    def hide(self) -> None:
        """Fade out then hide the window."""
        self._target = 0.0

    def destroy(self) -> None:
        if self._after_id:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None

    # ── private ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)        # macOS window-level alpha
        win.config(bg="black")

        # Position at bottom-centre of primary display
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x  = (sw - _W) // 2
        y  = sh - _H - 28           # 28 px above the macOS Dock
        win.geometry(f"{_W}x{_H}+{x}+{y}")

        canvas = tk.Canvas(win, width=_W, height=_H,
                           bg="black", highlightthickness=0)
        canvas.pack()

        self._win    = win
        self._canvas = canvas
        win.withdraw()

    def _ensure_loop(self) -> None:
        if self._after_id is None:
            self._tick()

    def _tick(self) -> None:
        self._after_id = None
        if self._win is None:
            return

        step = 1.0 / _FADE_STEPS
        if self._target > self._alpha:
            self._alpha = min(1.0, self._alpha + step)
        elif self._target < self._alpha:
            self._alpha = max(0.0, self._alpha - step)

        if self._alpha <= 0.001 and self._target <= 0.0:
            # Fully hidden — stop animating and withdraw
            try:
                self._win.attributes("-alpha", 0.0)
                self._win.withdraw()
            except Exception:
                pass
            return

        self._t += _TICK_MS / 1000.0

        frame = _make_frame(self._t, self._alpha, _W, _H)
        tk_img = ImageTk.PhotoImage(frame)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=tk_img)
        self._tk_img = tk_img   # keep reference

        # Sync macOS window transparency
        try:
            self._win.attributes("-alpha", min(1.0, self._alpha + 0.05))
        except Exception:
            pass

        self._after_id = self._win.after(_TICK_MS, self._tick)

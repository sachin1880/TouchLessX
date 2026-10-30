"""Siri-style wake-word overlay — full-screen-width animated gradient wave.

Appears at the BOTTOM of the screen the moment "Hey Computer" is heard.
Disappears (fades out) once the voice command is processed.

Visual design (iOS 16 Siri style):
  • Full-screen-width dark pill anchored to the bottom edge
  • 4 smooth sine-wave bands: electric-blue, cyan, violet, pink
  • Each band scrolls horizontally at a different speed
  • Central pulsing mic indicator + "Listening…" text
  • Top edge fades to black so the bar looks like it floats out of the dock
  • Whole window fades in/out with window-level alpha
"""
from __future__ import annotations

import math
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk

# ── sizing ─────────────────────────────────────────────────────────────────────
_H          = 140          # bar height
_TICK_MS    = 33           # ~30 fps
_FADE_STEPS = 12           # frames to fade in / fade out

# ── palette (RGBA) ─────────────────────────────────────────────────────────────
_BG         = (4, 8, 22)          # very dark navy (also canvas / win bg)

# Wave bands — (base_hue_rgba, speed, amplitude, y_offset_from_centre)
# Each band is a smooth horizontal sine curve drawn as a filled ribbon
_BANDS = [
    # color RGBA             x-scroll  y-amp  ribbon-height  y-off
    ((0,   190, 255, 200),   1.40,     18,    28,            -10),   # electric blue
    ((80,  130, 255, 170),   0.85,     22,    24,              4),   # indigo
    ((130,  60, 240, 160),   1.10,     16,    20,             14),   # violet
    ((240,  60, 180, 150),   1.65,     12,    16,              8),   # pink
]

# ── helpers ────────────────────────────────────────────────────────────────────

def _cl(v: float) -> int:
    return max(0, min(255, int(v)))


def _alpha_scale(rgba: tuple, a: float) -> tuple:
    return (*rgba[:3], _cl(rgba[3] * a))


def _make_frame(t: float, alpha: float, W: int, H: int) -> Image.Image:
    """Render one animation frame."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── dark background ────────────────────────────────────────────────────────
    bg_alpha = _cl(230 * alpha)
    draw.rectangle([0, 0, W, H], fill=(*_BG, bg_alpha))

    # ── top-edge fade: thin gradient strip (dark → transparent) ───────────────
    fade_h = H // 3
    for row in range(fade_h):
        ratio = 1.0 - row / fade_h          # 1 at top → 0 at fade_h
        overlay_a = _cl(230 * alpha * ratio)
        draw.rectangle([0, row, W, row + 1],
                       fill=(*_BG, overlay_a))

    # ── wave bands ─────────────────────────────────────────────────────────────
    CY = int(H * 0.62)           # baseline — slightly below centre
    samples = W + 1
    step = W / (samples - 1)

    for color, xspd, amp, ribbon_h, y_off in _BANDS:
        phase = t * xspd
        # Build ribbon: upper and lower edge of the wave
        upper, lower = [], []
        for i in range(samples):
            x = int(i * step)
            s1 = math.sin(phase + i * 0.055) * amp
            s2 = math.sin(phase * 1.5 + i * 0.031 + 1.2) * (amp * 0.45)
            y_mid = CY + y_off + s1 + s2
            half  = ribbon_h / 2
            upper.append((x, y_mid - half))
            lower.append((x, y_mid + half))

        poly = upper + list(reversed(lower))
        draw.polygon(poly, fill=_alpha_scale(color, alpha * 0.90))

    # ── bright center glow line across the bar ─────────────────────────────────
    glow_y = int(H * 0.62)
    for w, a in [(6, 0.18), (2, 0.38)]:
        col = _cl(255 * alpha * a)
        draw.rectangle([0, glow_y - w, W, glow_y + w],
                       fill=(80, 200, 255, col))

    # ── pulsing mic circle (centre) ────────────────────────────────────────────
    cx = W // 2
    cy = int(H * 0.50)
    pulse = (math.sin(t * 3.0) + 1) / 2           # 0-1
    mic_r = int(18 + 4 * pulse)
    halo_r = int(30 + 10 * pulse)

    # outer halo
    halo_a = _cl(90 * alpha * (0.5 + 0.5 * pulse))
    draw.ellipse([cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r],
                 fill=(0, 190, 255, halo_a))
    # inner fill
    draw.ellipse([cx - mic_r, cy - mic_r, cx + mic_r, cy + mic_r],
                 fill=(10, 20, 50, _cl(220 * alpha)))
    # rim
    draw.ellipse([cx - mic_r, cy - mic_r, cx + mic_r, cy + mic_r],
                 outline=(0, 200, 255, _cl(220 * alpha)), width=2)
    # mic glyph (simple vertical line + base arc)
    lh = int(mic_r * 0.55)
    lw = max(2, int(mic_r * 0.18))
    mic_col = (0, 220, 255, _cl(210 * alpha))
    draw.rectangle([cx - lw, cy - lh, cx + lw, cy + lh], fill=mic_col)
    # arc base
    draw.arc([cx - lw * 3, cy, cx + lw * 3, cy + lh],
             start=0, end=180, fill=mic_col, width=max(1, lw))

    # ── "Listening…" text ──────────────────────────────────────────────────────
    text_y = int(H * 0.84)
    text_a = _cl(200 * alpha)
    # Draw each character manually using draw.text with a font
    # Use default PIL font to avoid missing font errors
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()
    label = "Listening…"
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = len(label) * 8
    tx = (W - tw) // 2
    draw.text((tx, text_y - 9), label, font=font,
              fill=(160, 220, 255, text_a))

    return img


class SiriWave:
    """Full-screen-width Siri-style bottom bar.

    Usage::
        wave = SiriWave(root)
        wave.show()    # on "Hey Computer"
        wave.hide()    # when command arrives
    """

    def __init__(self, root: tk.Misc) -> None:
        self._root    = root
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._tk_img: ImageTk.PhotoImage | None = None
        self._t       = 0.0
        self._alpha   = 0.0
        self._target  = 0.0
        self._after_id: str | None = None
        self._W       = 0
        self._build()

    # ── public ────────────────────────────────────────────────────────────────

    def show(self) -> None:
        if self._win:
            self._win.deiconify()
            self._win.lift()
            self._win.attributes("-topmost", True)
        self._target = 1.0
        if self._after_id is None:
            self._tick()

    def hide(self) -> None:
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
        win.attributes("-alpha", 0.0)

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()

        # Full screen width, anchored to the very bottom
        W = sw
        x = 0
        y = sh - _H           # sit flush with the bottom edge

        bg_hex = f"#{_BG[0]:02x}{_BG[1]:02x}{_BG[2]:02x}"
        win.config(bg=bg_hex)
        win.geometry(f"{W}x{_H}+{x}+{y}")

        canvas = tk.Canvas(win, width=W, height=_H,
                           bg=bg_hex, highlightthickness=0)
        canvas.pack()

        self._win    = win
        self._canvas = canvas
        self._W      = W
        win.withdraw()

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
            try:
                self._win.attributes("-alpha", 0.0)
                self._win.withdraw()
            except Exception:
                pass
            return

        self._t += _TICK_MS / 1000.0

        frame  = _make_frame(self._t, self._alpha, self._W, _H)
        tk_img = ImageTk.PhotoImage(frame)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=tk_img)
        self._tk_img = tk_img

        try:
            # Keep window visible but use per-pixel alpha via PIL
            self._win.attributes("-alpha", min(1.0, 0.06 + self._alpha * 0.94))
        except Exception:
            pass

        self._after_id = self._win.after(_TICK_MS, self._tick)

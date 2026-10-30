"""Compact JARVIS floating HUD — replaces the full-screen agent overlay.

A small, always-on-top card (390×130px) that sits bottom-left above the dock.
It shows:
  • Current state (listening / thinking / executing / speaking)
  • What was heard
  • What the agent is doing right now
  • The final response (auto-dismisses after 6 s)

No full-screen takeover. Clean, minimal, out of the way.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

# ── dimensions & colours ──────────────────────────────────────────────────────
_W       = 420
_H       = 140
_PAD     = 20           # margin above dock
_TICK_MS = 40           # animation tick

_BG  = (6, 10, 22)
_BG2 = (10, 18, 36)

_TEAL   = (0,   212, 255)
_VIOLET = (130,  60, 240)
_GOLD   = (255, 200,  68)
_GREEN  = ( 80, 230, 100)
_RED    = (255,  70,  70)
_DIM    = (40,  70,  90)
_WHITE  = (200, 230, 255)

_STATE_COLOR = {
    "listening":  _TEAL,
    "thinking":   _VIOLET,
    "executing":  _GOLD,
    "speaking":   _GREEN,
    "success":    _GREEN,
    "error":      _RED,
    "idle":       _DIM,
}
_STATE_ICON = {
    "listening":  "◉",
    "thinking":   "⟳",
    "executing":  "▶",
    "speaking":   "♪",
    "success":    "✓",
    "error":      "✕",
    "idle":       "○",
}


def _cl(v: float) -> int:
    return max(0, min(255, int(v)))


def _make_frame(
    state: str,
    heard: str,
    status: str,
    progress: float,
    t: float,
    alpha: float,
    W: int,
    H: int,
) -> Image.Image:
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pulse = (math.sin(t * 3.5) + 1) / 2
    col   = _STATE_COLOR.get(state, _DIM)
    bg_a  = _cl(230 * alpha)

    # ── rounded pill background ───────────────────────────────────────────────
    r = 20
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=r,
                            fill=(*_BG, bg_a))
    # inner highlight
    draw.rounded_rectangle([1, 1, W - 2, H - 2], radius=r - 1,
                            outline=(*col, _cl(60 * alpha)), width=1)

    # ── left accent bar ────────────────────────────────────────────────────────
    ab_h = int(H * 0.55 + H * 0.15 * pulse)
    ab_y0 = (H - ab_h) // 2
    draw.rounded_rectangle([12, ab_y0, 16, ab_y0 + ab_h], radius=2,
                            fill=(*col, _cl(220 * alpha)))

    # ── state icon + label ────────────────────────────────────────────────────
    icon   = _STATE_ICON.get(state, "○")
    slabel = state.upper()

    # Draw text with default font (no external font needed)
    try:
        from PIL import ImageFont
        font_sm  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
        font_md  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
        font_lg  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        font_icon= ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except Exception:
        font_sm = font_md = font_lg = font_icon = ImageFont.load_default()

    # State label (top-left, after accent bar)
    draw.text((26, 14), f"{icon}  {slabel}", font=font_sm,
              fill=(*col, _cl(200 * alpha)))

    # ── heard text ────────────────────────────────────────────────────────────
    if heard:
        heard_short = heard[:52] + ("…" if len(heard) > 52 else "")
        draw.text((26, 34), f'"{heard_short}"', font=font_sm,
                  fill=(*_WHITE, _cl(140 * alpha)))

    # ── status / response text ────────────────────────────────────────────────
    if status:
        status_short = status[:62] + ("…" if len(status) > 62 else "")
        draw.text((26, 54), status_short, font=font_md,
                  fill=(*_WHITE, _cl(200 * alpha)))

    # ── JARVIS wordmark (bottom-right) ────────────────────────────────────────
    draw.text((W - 14, H - 16), "JARVIS", font=font_sm,
              fill=(*_DIM, _cl(120 * alpha)), anchor="rs")

    # ── progress bar (bottom edge) ────────────────────────────────────────────
    if progress > 0.02:
        bw = int((W - 32) * progress)
        draw.rounded_rectangle([16, H - 10, 16 + bw, H - 6], radius=2,
                                fill=(*col, _cl(180 * alpha)))
        draw.rounded_rectangle([16, H - 10, W - 16, H - 6], radius=2,
                                outline=(*_DIM, _cl(80 * alpha)), width=1)

    # ── animated dot (top-right, shows only while active) ─────────────────────
    if state not in ("idle", "success", "error"):
        dot_r = int(5 + 3 * pulse)
        dot_x = W - 22
        dot_y = 20
        draw.ellipse([dot_x - dot_r, dot_y - dot_r,
                      dot_x + dot_r, dot_y + dot_r],
                     fill=(*col, _cl(180 * alpha)))

    return img


class JarvisHUD:
    """Small floating JARVIS status card.

    show_* methods update the state and trigger a repaint.
    The card auto-dismisses 6 seconds after show_done().
    """

    def __init__(self, root: tk.Misc) -> None:
        self._root    = root
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._tk_img: ImageTk.PhotoImage | None = None

        self._state    = "idle"
        self._heard    = ""
        self._status   = ""
        self._progress = 0.0

        self._alpha    = 0.0
        self._target   = 0.0
        self._t        = 0.0
        self._after_id: str | None = None
        self._dismiss_after: float = 0.0   # monotonic time to auto-hide

        self._closed = False
        self._build()

    # ── public ────────────────────────────────────────────────────────────────

    def show_listening(self, heard_preview: str = "") -> None:
        self._state  = "listening"
        self._heard  = heard_preview
        self._status = "Listening…"
        self._progress = 0.0
        self._dismiss_after = 0.0
        self._show()

    def show_thinking(self, heard: str = "") -> None:
        self._state  = "thinking"
        self._heard  = heard
        self._status = "Thinking…"
        self._progress = 0.0
        self._dismiss_after = 0.0
        self._show()

    def show_executing(self, step: str, progress: float = 0.0) -> None:
        self._state    = "executing"
        self._status   = step
        self._progress = progress
        self._dismiss_after = 0.0
        self._show()

    def show_response(self, text: str) -> None:
        self._state  = "speaking"
        self._status = text
        self._progress = 1.0
        self._dismiss_after = 0.0
        self._show()

    def show_done(self, result: str) -> None:
        self._state    = "success"
        self._status   = result
        self._progress = 1.0
        self._dismiss_after = time.monotonic() + 6.0
        self._show()

    def show_error(self, msg: str) -> None:
        self._state    = "error"
        self._status   = msg
        self._progress = 0.0
        self._dismiss_after = time.monotonic() + 5.0
        self._show()

    def hide(self) -> None:
        self._target = 0.0

    def close(self) -> None:
        self._closed = True
        self.hide()

    # public aliases that match the AgentWindow interface so ui.py works
    def set_listening(self, text: str = "") -> None:
        self.show_listening(text)

    def set_thinking(self, text: str = "", mode: str = "") -> None:
        self.show_thinking(text)

    def set_executing(self, task: str = "", progress: float = 0.0) -> None:
        self.show_executing(task, progress)

    def reopen(self) -> None:
        self._closed = False
        self._show()

    # ── private ───────────────────────────────────────────────────────────────

    def _show(self) -> None:
        self._target = 1.0
        if self._win:
            try:
                self._win.deiconify()
                self._win.lift()
                self._win.attributes("-topmost", True)
            except Exception:
                pass
        if self._after_id is None:
            self._tick()

    def _build(self) -> None:
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x  = 20
        y  = sh - _H - _PAD - 60   # above dock

        bg_hex = f"#{_BG[0]:02x}{_BG[1]:02x}{_BG[2]:02x}"
        win.config(bg=bg_hex)
        win.geometry(f"{_W}x{_H}+{x}+{y}")

        canvas = tk.Canvas(win, width=_W, height=_H,
                           bg=bg_hex, highlightthickness=0)
        canvas.pack()

        self._win    = win
        self._canvas = canvas
        win.withdraw()

    def _tick(self) -> None:
        self._after_id = None
        if self._win is None or self._closed:
            return

        # Auto-dismiss
        if self._dismiss_after > 0 and time.monotonic() > self._dismiss_after:
            self._target = 0.0
            self._dismiss_after = 0.0

        step = 1.0 / 10
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

        frame  = _make_frame(
            self._state, self._heard, self._status, self._progress,
            self._t, self._alpha, _W, _H,
        )
        tk_img = ImageTk.PhotoImage(frame)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=tk_img)
        self._tk_img = tk_img

        try:
            self._win.attributes("-alpha", min(1.0, 0.05 + self._alpha * 0.95))
        except Exception:
            pass

        self._after_id = self._win.after(_TICK_MS, self._tick)

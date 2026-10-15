"""HUD-style widgets for the Holo Desk: a circular gauge with a soft glow and an
optional slowly turning outer ring. Drawn on a plain Tk canvas - CustomTkinter has no
circular widgets - so colours are resolved for the current theme on every redraw."""
from __future__ import annotations

import tkinter as tk

from .colors import mix
from .ui_widgets import ACCENT, PANEL, TEXT, TEXT_DIM, resolve

ANIMATION_MS = 50  # 20 frames a second for the turning ring: smooth, and cheap


class HudRing(tk.Canvas):
    def __init__(self, parent, size: int = 120, thickness: int = 8, background=PANEL, spin: bool = False,
                 title_size: int = 0) -> None:
        super().__init__(parent, width=size, height=size, highlightthickness=0, bd=0, bg=resolve(background))
        self.size, self.thickness, self.background, self.spin = size, thickness, background, spin
        self.title_size = title_size or max(10, size // 9)
        self.value, self.text, self.sub = 0.0, "", ""
        self.colour = None  # None = the accent colour
        self._angle = 0
        self._drawn = None
        if spin:
            self.after(ANIMATION_MS, self._turn)

    def set(self, value: float, text: str, sub: str = "", colour=None) -> None:
        self.value = max(0.0, min(1.0, value))
        self.text, self.sub, self.colour = text, sub, colour
        self.redraw()

    def redraw(self) -> None:
        bg = resolve(self.background)
        accent = resolve(self.colour or ACCENT)
        state = (self.value, self.text, self.sub, accent, bg, self._angle)
        if state == self._drawn:
            return
        self._drawn = state
        self.configure(bg=bg)
        self.delete("all")
        c, t = self.size / 2, self.thickness
        r = c - t - (8 if self.spin else 3)
        box = (c - r, c - r, c + r, c + r)
        self.create_oval(*box, outline=mix(accent, bg, 0.82), width=t)
        extent = -359.9 * self.value
        if self.value > 0:
            # A glow: wider, fainter copies of the arc underneath the arc itself.
            for extra, fade in ((t + 8, 0.86), (t + 4, 0.7)):
                self.create_arc(*box, start=90, extent=extent, style="arc", outline=mix(accent, bg, fade), width=extra)
            self.create_arc(*box, start=90, extent=extent, style="arc", outline=accent, width=t)
        if self.spin:
            outer = r + t / 2 + 5
            ring = (c - outer, c - outer, c + outer, c + outer)
            for offset in (0, 120, 240):
                self.create_arc(*ring, start=self._angle + offset, extent=70, style="arc",
                                outline=mix(accent, bg, 0.45), width=2)
        text_colour = resolve(TEXT)
        if self.sub:
            self.create_text(c, c - self.title_size * 0.45, text=self.text, fill=text_colour,
                             font=("Helvetica", self.title_size, "bold"))
            self.create_text(c, c + self.title_size * 0.75, text=self.sub, fill=resolve(TEXT_DIM),
                             font=("Helvetica", max(9, int(self.title_size * 0.6))))
        else:
            self.create_text(c, c, text=self.text, fill=text_colour, font=("Helvetica", self.title_size, "bold"))

    def _turn(self) -> None:
        if not self.winfo_exists():
            return
        if self.winfo_ismapped():
            self._angle = (self._angle - 3) % 360
            self.redraw()
        self.after(ANIMATION_MS, self._turn)

"""Full-screen AI Agent overlay — cinematic holographic HUD.

Single-canvas design (one tk.Canvas for everything) avoids macOS z-order issues.

Layout
------
  Header   (64 px)  — wordmark, scan bar, language selector
  Left     (200 px) — system stats panels
  Centre   (flex)   — PIL orb + animated orbital tool icons
  Right    (200 px) — voice analysis + conversation log
  Strip    (90 px)  — state chip, heard text, response

Orbital icons
-------------
  9 tool icons float in a slow-rotating ellipse around the orb.
  Rendered as layered canvas items (clickable, no PIL needed).
  Each icon: outer glow oval → dark fill oval → symbol text → name label.

Voice bug fix
-------------
  close() uses withdraw() not destroy() so the macOS fullscreen Space is
  kept alive.  reopen() brings it back.  This fixes "second 'Hey Computer'
  has no effect" on macOS.
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
from typing import Callable, Optional

from PIL import Image as _PIL_Image, ImageTk as _PIL_ImageTk

from .ui_avatar_canvas import AvatarCanvas
from .agent_tts import AgentTTS
from .agent_theme import get as _theme
from .agent_lang import (
    LangCode, LANG_LABELS, LANG_ORDER,
    load_preference, save_preference, resolve_language,
)

# ── palette ───────────────────────────────────────────────────────────────────
_BG      = "#03070f"
_BG_RGB  = (3, 7, 15)
_TEAL    = "#00d4ff"
_TQDIM   = "#00384a"
_GOLD    = "#ffc844"
_WHITE   = "#ddeeff"
_DIM     = "#1a2d3e"
_DIM2    = "#2a4555"
_GREEN   = "#50e664"
_RED     = "#ff4646"
_ORANGE  = "#ff8c28"
_BLUE    = "#50a0ff"
_VIOLET  = "#8a64f0"

# ── fonts  (Helvetica Neue = clean macOS system font; Courier for data) ───────
_HN   = "Helvetica Neue"
_MONO = "Courier"

_F_TINY  = (_HN,   8)
_F_SM    = (_HN,   9)
_F_MD    = (_HN,  10)
_F_LG    = (_HN,  11, "bold")
_F_XL    = (_HN,  13, "bold")
_F_HEAD  = (_HN,  15, "bold")
_F_DATA  = (_MONO, 8)
_F_DATA2 = (_MONO, 9)
_F_IC    = (_HN,   9, "bold")   # icon abbreviation
_F_LBL   = (_HN,   8)           # icon name

_PANEL_W  = 200
_HEADER_H = 64
_STRIP_H  = 88

# ── orbital tool icons ────────────────────────────────────────────────────────
# (abbreviation, hex-color, linked-agent-state, display-name)
_TOOLS: list[tuple[str, str, str, str]] = [
    ("AI",  _TEAL,   "thinking",  "AI Chat"),
    ("{ }",  _GREEN,  "coding",    "Code"),
    ("⌃⌘",  _ORANGE, "executing", "Control"),
    ("≡",   _GOLD,   "research",  "Files"),
    ("⊕",   _VIOLET, "research",  "Browser"),
    ("◈",   _TEAL,   "listening", "Voice"),
    ("☏",   _GREEN,  "phone",     "Phone"),
    ("◷",   _BLUE,   "meeting",   "Calendar"),
    ("⚙",   _WHITE,  "focus",     "Settings"),
]

_ICON_R    = 26      # circle radius (px)
_ORBIT_SPD = 3.5     # degrees per second

# ── color helpers ─────────────────────────────────────────────────────────────

def _dim_hex(h: str, f: float) -> str:
    """Return *h* (e.g. '#00d4ff') darkened to fraction *f* (0-1)."""
    r = int(h[1:3], 16); g = int(h[3:5], 16); b = int(h[5:7], 16)
    return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"


class AgentWindow:
    """Full-screen AI agent overlay."""

    def __init__(self, app, on_close: Optional[Callable[[], None]] = None) -> None:
        self.app       = app
        self._on_close = on_close
        self._tts      = AgentTTS()
        self._closed   = False
        self._auto_after: Optional[str] = None
        self._lang: LangCode = load_preference()

        self.window = tk.Toplevel(app)
        self.window.configure(bg=_BG)
        self.window.title("JARVIS")
        sw = app.winfo_screenwidth()
        sh = app.winfo_screenheight()
        self.window.geometry(f"{sw}x{sh}+0+0")
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-topmost",    True)
        self.window.bind("<Escape>", lambda _: self.close())

        self._sw, self._sh = sw, sh
        self._start_time   = time.time()
        self._req_count    = 0
        self._tok_count    = 0
        self._log_lines: list[tuple[str, str]] = [
            ("SYS", "Jarvis initialised"),
            ("SYS", "Awaiting wake word…"),
        ]
        self._bars = {
            "neural": 0.72, "voice": 0.15, "llm": 0.12, "mem": 0.31,
            "f0": 0.04, "f1": 0.06, "f2": 0.08, "f3": 0.05, "f4": 0.03,
        }
        self._scan_step   = 0
        self._icon_t0     = time.time()
        self._tool_items: list[tuple[int, int, int, int]] = []

        self._build_ui()
        self.window.after(120, self.window.focus_force)

    # ─────────────────────────────────────────────────────── build
    def _build_ui(self) -> None:
        sw, sh = self._sw, self._sh
        W = self.window
        use_panels = sw >= 750
        content_h  = sh - _HEADER_H - _STRIP_H

        # ── ONE canvas ───────────────────────────────────────────────────────
        self._cv = tk.Canvas(W, width=sw, height=sh, bg=_BG,
                             highlightthickness=0)
        self._cv.pack(fill="both", expand=True)

        # ── Radial glow background ────────────────────────────────────────────
        self._draw_bg(sw, sh, use_panels)

        # ── Avatar area ───────────────────────────────────────────────────────
        pw         = _PANEL_W if use_panels else 0
        center_w   = sw - pw * 2
        center_x0  = pw

        av_h = content_h - 24
        av_w = max(340, min(center_w - 40, int(av_h * 0.76)))
        av_x = center_x0 + (center_w - av_w) // 2
        av_y = _HEADER_H + 12

        blank = _PIL_Image.new("RGB", (av_w, av_h), _BG_RGB)
        self._av_ref  = _PIL_ImageTk.PhotoImage(blank)
        self._av_item = self._cv.create_image(av_x, av_y, anchor="nw",
                                               image=self._av_ref)

        self._avatar = AvatarCanvas(W, width=av_w, height=av_h,
                                    ext_canvas=self._cv,
                                    ext_img_id=self._av_item)
        self._avatar.start()

        # Store orb geometry for icon positioning
        orb_r_px      = int(min(av_w, av_h) * 0.27)
        self._orb_cx  = av_x + av_w // 2
        self._orb_cy  = av_y + int(av_h * 0.40)
        self._icon_rx = int(av_w * 0.72)
        self._icon_ry = int(self._icon_rx * 0.36)

        # ── Orbital icons ─────────────────────────────────────────────────────
        self._build_orbital_icons()

        # ── Header ────────────────────────────────────────────────────────────
        # Left: exit + wordmark
        self._cv.create_text(18, _HEADER_H // 2 - 4, text="← EXIT",
                             fill=_TQDIM, anchor="w", font=_F_MD,
                             tags="exit_btn")
        self._cv.tag_bind("exit_btn", "<Button-1>", lambda _: self.close())
        self._cv.tag_bind("exit_btn", "<Enter>",
                          lambda _: self._cv.itemconfig("exit_btn", fill=_TEAL))
        self._cv.tag_bind("exit_btn", "<Leave>",
                          lambda _: self._cv.itemconfig("exit_btn", fill=_TQDIM))

        self._cv.create_text(90, 22, text="JARVIS", fill=_TEAL,
                             anchor="w", font=_F_HEAD)
        self._cv.create_text(90, 42, text="HUMANOID  AI  ASSISTANT",
                             fill=_DIM2, anchor="w", font=_F_TINY)

        # Scan progress bar
        self._cv.create_rectangle(220, 38, 460, 40, fill=_DIM, outline="")
        self._scan_rect = self._cv.create_rectangle(220, 38, 220, 40,
                                                     fill=_TEAL, outline="")

        # Right: version + language selector
        self._cv.create_text(sw - 14, 20, text="MERAAI  v2.0  ·  NEURAL ENGINE",
                             fill=_DIM2, anchor="e", font=_F_TINY)

        self._lang_items: dict[LangCode, int] = {}
        lx = sw - 14
        for lang in reversed(LANG_ORDER):
            lbl  = LANG_LABELS[lang]
            fill = _TEAL if lang == self._lang else _DIM2
            item = self._cv.create_text(
                lx, 42, text=lbl, anchor="e",
                font=(_HN, 9, "bold"), fill=fill, tags=f"lang_{lang}")
            self._lang_items[lang] = item
            self._cv.tag_bind(f"lang_{lang}", "<Button-1>",
                              lambda _, l=lang: self._set_lang(l))
            self._cv.tag_bind(f"lang_{lang}", "<Enter>",
                              lambda _, l=lang: self._cv.itemconfig(
                                  self._lang_items[l], fill=_WHITE))
            self._cv.tag_bind(f"lang_{lang}", "<Leave>",
                              lambda _, l=lang: self._cv.itemconfig(
                                  self._lang_items[l],
                                  fill=_TEAL if l == self._lang else _DIM2))
            lx -= 44
        self._cv.create_text(lx, 42, text="LANG ·", anchor="e",
                             fill=_DIM2, font=_F_TINY)

        # ── Progress bar ──────────────────────────────────────────────────────
        pb_y = sh - _STRIP_H - 20
        self._pb_track = self._cv.create_rectangle(
            av_x, pb_y, av_x + av_w, pb_y + 3,
            fill=_DIM, outline="", state="hidden")
        self._pb_fill_item = self._cv.create_rectangle(
            av_x, pb_y, av_x, pb_y + 3,
            fill=_ORANGE, outline="", state="hidden")
        self._pb_text_item = self._cv.create_text(
            av_x, pb_y + 10, text="", fill=_ORANGE,
            anchor="w", font=_F_TINY, state="hidden")
        self._pb_x, self._pb_y, self._pb_w = av_x, pb_y, av_w

        # ── State strip ───────────────────────────────────────────────────────
        sy = sh - _STRIP_H
        # divider with gradient dots
        for xi in range(sw // 5, sw * 4 // 5, 6):
            a = 1.0 - abs(xi - sw//2) / (sw * 0.35)
            c = _dim_hex(_TEAL, max(0, a * 0.5))
            self._cv.create_rectangle(xi, sy+1, xi+3, sy+2, fill=c, outline="")

        self._state_item = self._cv.create_text(
            sw // 2, sy + 18, text="◉  READY",
            fill=_TEAL, font=_F_LG)
        self._mode_item  = self._cv.create_text(
            sw // 2, sy + 36, text="",
            fill=_DIM2, font=_F_SM)
        self._heard_item = self._cv.create_text(
            sw // 2, sy + 54, text="Say  'Hey Computer'  to activate",
            fill=_GOLD, font=_F_MD, width=sw - 60)
        self._resp_item  = self._cv.create_text(
            sw // 2, sy + 72, text="",
            fill=_WHITE, font=(_HN, 11), width=sw - 60)

        self._use_panels = use_panels
        self._panel_y0   = _HEADER_H

        # ── Start loops ───────────────────────────────────────────────────────
        self._scan_loop()
        self._panel_loop()
        self._icon_loop()

    # ─────────────────────────────────────────────────────── background
    def _draw_bg(self, sw: int, sh: int, use_panels: bool) -> None:
        c = self._cv

        # ── Radial glow from screen centre ───────────────────────────────────
        # Draw concentric rectangles fading from near-black to teal glow centre
        import math as _math
        cx, cy = sw // 2, sh // 2
        for step in range(24, 0, -1):
            frac  = step / 24
            rw    = int(sw * 0.05 + sw * 0.55 * (1 - frac))
            rh    = int(sh * 0.05 + sh * 0.55 * (1 - frac))
            alpha = int(frac * 22)
            r = max(0, min(255, alpha))
            col = f"#{r//4:02x}{r//3:02x}{r:02x}"
            c.create_rectangle(cx - rw, cy - rh, cx + rw, cy + rh,
                               fill=col, outline="")

        # ── Visible grid (slightly brighter than before) ──────────────────────
        for x in range(0, sw, 60):
            c.create_line(x, 0, x, sh, fill="#060f1a", width=1)
        for y in range(0, sh, 60):
            c.create_line(0, y, sw, y, fill="#060f1a", width=1)

        # ── Header / strip dividers ───────────────────────────────────────────
        c.create_line(0, _HEADER_H, sw, _HEADER_H, fill=_DIM, width=1)
        c.create_line(0, sh - _STRIP_H, sw, sh - _STRIP_H, fill=_DIM, width=1)

        # ── Glow line under header ────────────────────────────────────────────
        for xi in range(0, sw, 3):
            a = 1.0 - abs(xi - sw / 2) / (sw * 0.50)
            if a > 0:
                col = _dim_hex(_TEAL, max(0, a * 0.50))
                c.create_rectangle(xi, _HEADER_H - 2, xi + 2, _HEADER_H,
                                   fill=col, outline="")
        # ── Glow line above strip ─────────────────────────────────────────────
        sy = sh - _STRIP_H
        for xi in range(0, sw, 3):
            a = 1.0 - abs(xi - sw / 2) / (sw * 0.50)
            if a > 0:
                col = _dim_hex(_TEAL, max(0, a * 0.30))
                c.create_rectangle(xi, sy, xi + 2, sy + 2,
                                   fill=col, outline="")

        if use_panels:
            c.create_line(_PANEL_W, _HEADER_H, _PANEL_W, sh - _STRIP_H,
                          fill=_DIM, width=1)
            c.create_line(sw - _PANEL_W, _HEADER_H, sw - _PANEL_W,
                          sh - _STRIP_H, fill=_DIM, width=1)

        # ── Corner brackets — larger and brighter ────────────────────────────
        L = 52
        for x1, y1, x2, y2, x3, y3 in [
            (14, 14,    14 + L, 14,    14,    14 + L),
            (sw-14, 14, sw-14-L, 14,   sw-14, 14 + L),
            (14, sh-14, 14 + L, sh-14, 14,    sh-14-L),
            (sw-14, sh-14, sw-14-L, sh-14, sw-14, sh-14-L),
        ]:
            c.create_line(x1, y1, x2, y2, fill=_DIM,  width=5)
            c.create_line(x1, y1, x3, y3, fill=_DIM,  width=5)
            c.create_line(x1, y1, x2, y2, fill=_TEAL, width=2)
            c.create_line(x1, y1, x3, y3, fill=_TEAL, width=2)

        c.create_text(sw - 12, sh - 8, text="MERAAI  ·  TouchlessX",
                      fill=_DIM2, anchor="se", font=_F_TINY)

    # ─────────────────────────────────────────────────────── orbital icons
    def _build_orbital_icons(self) -> None:
        for abbrev, color, _state, name in _TOOLS:
            outer_glow = self._cv.create_oval(
                0, 0, 1, 1, fill=_dim_hex(color, 0.08), outline="")
            mid_glow   = self._cv.create_oval(
                0, 0, 1, 1, fill=_dim_hex(color, 0.15), outline="")
            fill_oval  = self._cv.create_oval(
                0, 0, 1, 1, fill="#050d1a",
                outline=_dim_hex(color, 0.55), width=1)
            abbr_txt   = self._cv.create_text(
                0, 0, text=abbrev, fill=color, font=_F_IC)
            name_txt   = self._cv.create_text(
                0, 0, text=name, fill=_dim_hex(color, 0.5), font=_F_LBL)

            self._tool_items.append(
                (outer_glow, mid_glow, fill_oval, abbr_txt))
            # Store name item separately (5th element appended)
            self._tool_items[-1] = (outer_glow, mid_glow, fill_oval,
                                    abbr_txt, name_txt)   # type: ignore[assignment]

            for tag in (fill_oval, abbr_txt):
                self._cv.tag_bind(tag, "<Button-1>",
                                  lambda _, s=_state: self._on_tool_click(s))
                self._cv.tag_bind(tag, "<Enter>",
                                  lambda _, fo=fill_oval, co=color: self._cv.itemconfig(
                                      fo, fill=_dim_hex(co, 0.22),
                                      outline=co, width=2))
                self._cv.tag_bind(tag, "<Leave>",
                                  lambda _, fo=fill_oval, co=color: self._cv.itemconfig(
                                      fo, fill="#050d1a",
                                      outline=_dim_hex(co, 0.55), width=1))

    def _icon_loop(self) -> None:
        if self._closed:
            return
        elapsed = time.time() - self._icon_t0
        angle   = (elapsed * _ORBIT_SPD) % 360.0
        n       = len(_TOOLS)
        cx, cy  = self._orb_cx, self._orb_cy
        rx, ry  = self._icon_rx, self._icon_ry
        av_st   = self._avatar._state

        for i, (abbrev, color, linked, name) in enumerate(_TOOLS):
            ang    = math.radians(angle + i * (360 / n))
            bob    = math.sin(time.time() * 1.3 + i * 0.78) * 8
            ix     = int(cx + rx * math.cos(ang))
            iy     = int(cy + ry * math.sin(ang) + bob)
            active = (av_st == linked)
            r      = _ICON_R

            outer_glow, mid_glow, fill_oval, abbr_txt, name_txt = \
                self._tool_items[i]  # type: ignore[misc]

            gr1, gr2 = r + 14, r + 7
            self._cv.coords(outer_glow, ix-gr1, iy-gr1, ix+gr1, iy+gr1)
            self._cv.coords(mid_glow,   ix-gr2, iy-gr2, ix+gr2, iy+gr2)
            self._cv.coords(fill_oval,  ix-r,   iy-r,   ix+r,   iy+r)
            self._cv.coords(abbr_txt,   ix, iy)
            self._cv.coords(name_txt,   ix, iy + r + 11)

            if active:
                self._cv.itemconfig(outer_glow, fill=_dim_hex(color, 0.14))
                self._cv.itemconfig(mid_glow,   fill=_dim_hex(color, 0.22))
                self._cv.itemconfig(fill_oval,  fill=_dim_hex(color, 0.18),
                                    outline=color, width=2)
                self._cv.itemconfig(abbr_txt,   fill=color)
                self._cv.itemconfig(name_txt,   fill=_dim_hex(color, 0.75))
            else:
                self._cv.itemconfig(outer_glow, fill=_dim_hex(color, 0.06))
                self._cv.itemconfig(mid_glow,   fill=_dim_hex(color, 0.10))
                self._cv.itemconfig(fill_oval,  fill="#050d1a",
                                    outline=_dim_hex(color, 0.40), width=1)
                self._cv.itemconfig(abbr_txt,   fill=_dim_hex(color, 0.55))
                self._cv.itemconfig(name_txt,   fill=_dim_hex(color, 0.35))

        self.window.after(80, self._icon_loop)

    def _on_tool_click(self, state: str) -> None:
        self._avatar.set_state(state)
        th = _theme(state)
        self._ic(self._state_item, text=th.label, fill=th.accent_hex)
        self._ic(self._mode_item,  text=_MODE_HINT.get(state, state.upper()),
                 fill=_DIM2)

    # ─────────────────────────────────────────────────────── language
    def _set_lang(self, lang: LangCode) -> None:
        self._lang = lang
        save_preference(lang)
        for l, item in self._lang_items.items():
            self._cv.itemconfig(item, fill=_TEAL if l == lang else _DIM2)

    def get_effective_lang(self, text: str) -> LangCode:
        return resolve_language(text, self._lang)

    # ─────────────────────────────────────────────────────── scan bar
    def _scan_loop(self) -> None:
        if self._closed:
            return
        self._scan_step = (self._scan_step + 1) % 60
        w = int(240 * self._scan_step / 60)
        try:
            self._cv.coords(self._scan_rect, 220, 38, 220 + w, 40)
        except tk.TclError:
            return
        self.window.after(40, self._scan_loop)

    # ─────────────────────────────────────────────────────── panels
    def _panel_loop(self) -> None:
        if self._closed:
            return
        self._animate_bars()
        self._redraw_left()
        self._redraw_right()
        self.window.after(400, self._panel_loop)

    def _animate_bars(self) -> None:
        s = self._avatar._state
        if s in ("listening", "speaking", "phone"):
            self._bars["voice"]  = 0.55 + random.random() * 0.40
            for k in ("f0","f1","f2","f3","f4"):
                b = [0.10,0.20,0.30,0.15,0.05][["f0","f1","f2","f3","f4"].index(k)]
                self._bars[k] = b + random.random() * 0.60
        else:
            for k in ("f0","f1","f2","f3","f4"):
                self._bars[k] = max(0.02, self._bars[k] * 0.55 + random.random() * 0.05)
            self._bars["voice"] = max(0.05, self._bars["voice"] * 0.6)
        if s in ("thinking", "executing", "coding", "research"):
            self._bars["neural"] = 0.70 + random.random() * 0.28
            self._bars["llm"]    = 0.35 + random.random() * 0.60
        else:
            self._bars["neural"] = 0.50 + random.random() * 0.20
            self._bars["llm"]    = max(0.08, self._bars["llm"] * 0.7)
        self._bars["mem"] = 0.28 + random.random() * 0.12

    def _redraw_left(self) -> None:
        if not self._use_panels:
            return
        c = self._cv; c.delete("panel_left")
        ox, oy, PW, TG = 0, self._panel_y0, _PANEL_W, "panel_left"
        B = self._bars
        el = int(time.time() - self._start_time)
        h_s = str(el // 3600).zfill(2)
        m_s = str(el % 3600 // 60).zfill(2)
        s_s = str(el % 60).zfill(2)
        lat = f"{random.randint(28,54)} ms"

        def _sec(y: int, title: str) -> None:
            c.create_text(ox+12, oy+y, text=title.upper(),
                          fill=_TEAL, anchor="w", font=_F_TINY, tags=TG)
            c.create_line(ox+12, oy+y+10, ox+PW-12, oy+y+10,
                          fill=_DIM, width=1, tags=TG)

        def _bar(y: int, label: str, val: float, col: str = _TEAL) -> None:
            c.create_text(ox+12, oy+y, text=label, fill=_DIM2,
                          anchor="w", font=_F_TINY, tags=TG)
            tx, tw = ox+56, 100
            c.create_rectangle(tx, oy+y-3, tx+tw, oy+y+3,
                                fill="#040b14", outline="", tags=TG)
            fw = max(2, int(tw * val))
            c.create_rectangle(tx, oy+y-2, tx+fw, oy+y+2,
                                fill=col, outline="", tags=TG)
            c.create_text(tx+tw+5, oy+y, text=f"{int(val*100)}",
                          fill=_DIM2, anchor="w", font=_F_TINY, tags=TG)

        def _row(y: int, k: str, v: str, vc: str = _TEAL) -> None:
            c.create_text(ox+12,    oy+y, text=k, fill=_DIM2,
                          anchor="w", font=_F_TINY, tags=TG)
            c.create_text(ox+PW-12, oy+y, text=v, fill=vc,
                          anchor="e", font=_F_DATA, tags=TG)

        y = 20
        _sec(y, "System"); y += 14
        _bar(y, "Neural", B["neural"]);  y += 16
        _bar(y, "Voice",  B["voice"]);   y += 16
        _bar(y, "LLM",    B["llm"]);     y += 16
        _bar(y, "Memory", B["mem"]);     y += 18

        _sec(y, "Vitals"); y += 14
        _row(y, "Latency",  lat,              _TEAL);  y += 13
        _row(y, "Uptime",   f"{h_s}:{m_s}:{s_s}", _TEAL);  y += 13
        _row(y, "Requests", str(self._req_count), _TEAL);  y += 13
        _row(y, "Tokens",   str(self._tok_count), _TEAL);  y += 13
        _row(y, "Model",    "LOCAL",           _DIM2);  y += 18

        _sec(y, "Accuracy"); y += 14
        _bar(y, "STT", 0.96, _GREEN);  y += 16
        _bar(y, "NLU", 0.92, _GREEN);  y += 18

        _sec(y, "Agent"); y += 14
        _row(y, "Engine",  "LOCAL",   _DIM2);  y += 13
        _row(y, "Context", "JARVIS",  _DIM2);  y += 13
        _row(y, "Status",  "ONLINE",  _GREEN); y += 13
        lang_lbl = {"auto":"AUTO","english":"EN","hindi":"HI","hinglish":"MIX"
                    }.get(self._lang, "AUTO")
        _row(y, "Lang", lang_lbl, _TEAL)

    def _redraw_right(self) -> None:
        if not self._use_panels:
            return
        c = self._cv; c.delete("panel_right")
        sw, PW, TG = self._sw, _PANEL_W, "panel_right"
        ox, oy = sw - PW, self._panel_y0
        B = self._bars

        def _sec(y: int, title: str) -> None:
            c.create_text(ox+12, oy+y, text=title.upper(),
                          fill=_TEAL, anchor="w", font=_F_TINY, tags=TG)
            c.create_line(ox+12, oy+y+10, ox+PW-12, oy+y+10,
                          fill=_DIM, width=1, tags=TG)

        def _freq(y: int, label: str, val: float) -> None:
            c.create_text(ox+12, oy+y, text=label, fill=_DIM2,
                          anchor="w", font=_F_TINY, tags=TG)
            tx, tw = ox+48, 108
            c.create_rectangle(tx, oy+y-3, tx+tw, oy+y+3,
                                fill="#040b14", outline="", tags=TG)
            fw = max(2, int(tw * val))
            c.create_rectangle(tx, oy+y-2, tx+fw, oy+y+2,
                                fill=_TEAL, outline="", tags=TG)

        def _row(y: int, k: str, v: str, vc: str = _TEAL) -> None:
            c.create_text(ox+12,    oy+y, text=k, fill=_DIM2,
                          anchor="w", font=_F_TINY, tags=TG)
            c.create_text(ox+PW-12, oy+y, text=v, fill=vc,
                          anchor="e", font=_F_DATA, tags=TG)

        y = 20
        _sec(y, "Voice"); y += 14
        for lbl, key in [("100Hz","f0"),("400Hz","f1"),("1kHz","f2"),
                         ("4kHz","f3"),("8kHz","f4")]:
            _freq(y, lbl, B[key]); y += 15
        y += 6

        _sec(y, "Conversation"); y += 14
        for role, text in self._log_lines[-6:]:
            rc = (_TEAL if role == "SYS"
                  else _GOLD if role == "YOU" else _WHITE)
            c.create_text(ox+12, oy+y, text=role[:3], fill=rc,
                          anchor="w", font=_F_TINY, tags=TG)
            short = text[:22] + ("…" if len(text) > 22 else "")
            c.create_text(ox+38, oy+y, text=short, fill=_DIM2,
                          anchor="w", font=_F_TINY, tags=TG)
            y += 13
        y += 6

        _sec(y, "Context"); y += 14
        for k, v in [
            ("Session", "ACTIVE" if self._req_count > 0 else "FRESH"),
            ("Project", "TouchlessX"),
            ("Wake",    "Hey Computer"),
        ]:
            _row(y, k, v); y += 13

    # ─────────────────────────────────────────────────────── 12 state methods
    def set_idle(self) -> None:
        self._cancel_auto()
        self._set_base("idle", "◉  READY", _TEAL)
        self._ic(self._heard_item,
                 text="Say  'Hey Computer'  to activate", fill=_GOLD)
        self._ic(self._resp_item,  text="")
        self._ic(self._mode_item,  text="")
        self._hide_progress()

    def set_listening(self) -> None:
        self._cancel_auto()
        self._set_base("listening", "◉  LISTENING", _TEAL)
        self._ic(self._heard_item, text="Speak your command…", fill=_GOLD)
        self._ic(self._resp_item,  text="")
        self._ic(self._mode_item,  text="")
        self._hide_progress()
        self._add_log("SYS", "Wake word detected")

    def set_thinking(self, heard: str = "", mode: str = "") -> None:
        self._cancel_auto()
        av_st = (mode if mode in
                 ("coding","research","meeting","phone","focus")
                 else "thinking")
        th    = _theme(av_st)
        chip  = th.label if av_st != "thinking" else "⟳  PROCESSING"
        self._set_base(av_st, chip, th.accent_hex)
        if heard:
            self._ic(self._heard_item, text=f'"{heard}"', fill=_GOLD)
        self._ic(self._mode_item, text=_MODE_HINT.get(mode, ""), fill=_DIM2)
        self._hide_progress()
        if heard:
            self._add_log("YOU", heard)
        self._req_count += 1

    def set_executing(self, task: str = "", progress: float = 0.0) -> None:
        self._cancel_auto()
        self._set_base("executing", "▶  EXECUTING", _ORANGE)
        if task:
            self._ic(self._heard_item, text=task, fill=_ORANGE)
        self._avatar.set_progress(progress)
        self._show_progress(task, progress)

    def update_progress(self, progress: float, task: str = "") -> None:
        if self._closed:
            return
        self._avatar.set_progress(progress)
        try:
            x, y, w = self._pb_x, self._pb_y, self._pb_w
            self._cv.coords(self._pb_fill_item, x, y, x + int(w * progress), y + 3)
            if task:
                self._ic(self._pb_text_item, text=task)
        except tk.TclError:
            pass

    def set_success(self, msg: str = "") -> None:
        self._cancel_auto()
        self._set_base("success", "✓  SUCCESS", _GREEN)
        self._ic(self._heard_item, text="", fill=_GOLD)
        self._ic(self._resp_item,  text=msg or "Task completed.", fill=_GREEN)
        self._ic(self._mode_item,  text="")
        self._hide_progress()
        self._add_log("SYS", msg or "Task completed")
        try:
            self._auto_after = self.window.after(2500, self.set_idle)
        except tk.TclError:
            pass

    def set_error(self, msg: str = "") -> None:
        self._cancel_auto()
        self._set_base("error", "⚠  ERROR", _RED)
        short = (msg[:180] + "…") if len(msg) > 180 else msg
        self._ic(self._heard_item, text="Something went wrong.", fill=_RED)
        self._ic(self._resp_item,  text=short, fill="#ff8888")
        self._ic(self._mode_item,
                 text="Say a new command or press Esc", fill=_DIM2)
        self._hide_progress()
        self._add_log("ERR", msg)

    def set_speaking(self, response: str) -> None:
        self._cancel_auto()
        self._set_base("speaking", "◈  SPEAKING", _GOLD)
        short = (response[:240] + "…") if len(response) > 240 else response
        self._ic(self._resp_item, text=short, fill=_WHITE)
        self._ic(self._mode_item, text="")
        self._hide_progress()

    def set_meeting(self, details: str = "") -> None:
        self._cancel_auto()
        self._set_base("meeting", "◉  MEETING", _BLUE)
        if details:
            self._ic(self._heard_item, text=details, fill=_GOLD)
        self._ic(self._mode_item, text="Calendar context", fill=_DIM2)
        self._hide_progress()

    def set_phone(self, contact: str = "") -> None:
        self._cancel_auto()
        self._set_base("phone", "☎  CALL", _GREEN)
        if contact:
            self._ic(self._heard_item, text=contact, fill=_GOLD)
        self._ic(self._mode_item, text="Phone / communication", fill=_DIM2)
        self._hide_progress()

    def set_focus(self) -> None:
        self._cancel_auto()
        self._set_base("focus", "◉  FOCUS", "#007896")
        self._ic(self._heard_item,
                 text="Focus mode — reduced distractions.", fill=_DIM2)
        self._ic(self._resp_item, text="")
        self._ic(self._mode_item, text="")
        self._hide_progress()

    # ─────────────────────────────────────────────────────── response
    def show_response(self, response: str, mode: str = "") -> None:
        if self._closed:
            return
        try:
            self.window.after(0,
                              lambda: self._do_show_response(response, mode))
        except RuntimeError:
            pass

    def _do_show_response(self, response: str, mode: str = "") -> None:
        if self._closed:
            return
        self.set_speaking(response)
        self._tok_count += len(response.split())
        self._add_log("AI", response)
        lang = self.get_effective_lang(response)
        self._tts.speak(
            response, lang=lang,
            on_end=lambda: (
                self.window.after(0, self.set_idle)
                if not self._closed else None),
        )

    # ─────────────────────────────────────────────────────── helpers
    def _set_base(self, av_state: str, label: str, color: str) -> None:
        self._avatar.set_state(av_state)
        self._ic(self._state_item, text=label, fill=color)

    def _show_progress(self, task: str, progress: float) -> None:
        try:
            x, y, w = self._pb_x, self._pb_y, self._pb_w
            self._cv.itemconfig(self._pb_track, state="normal")
            fw = max(0, int(w * progress))
            self._cv.coords(self._pb_fill_item, x, y, x + fw, y + 3)
            self._cv.itemconfig(self._pb_fill_item, state="normal")
            self._cv.itemconfig(self._pb_text_item, text=task, state="normal")
        except tk.TclError:
            pass

    def _hide_progress(self) -> None:
        try:
            for item in (self._pb_track, self._pb_fill_item,
                         self._pb_text_item):
                self._cv.itemconfig(item, state="hidden")
        except tk.TclError:
            pass

    def _add_log(self, role: str, text: str) -> None:
        self._log_lines.append((role[:3].upper(), text))
        if len(self._log_lines) > 10:
            self._log_lines = self._log_lines[-10:]

    def _cancel_auto(self) -> None:
        if self._auto_after is not None:
            try:
                self.window.after_cancel(self._auto_after)
            except tk.TclError:
                pass
            self._auto_after = None

    def _ic(self, item_id: int, **kw) -> None:
        try:
            self._cv.itemconfig(item_id, **kw)
        except tk.TclError:
            pass

    # ─────────────────────────────────────────────────────── close / reopen
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_auto()
        self._avatar.stop()
        self._tts.stop()
        try:
            # Withdraw (hide) instead of destroy — avoids macOS blocking
            # rapid fullscreen Space creation on second activation.
            self.window.attributes("-fullscreen", False)
            self.window.withdraw()
        except tk.TclError:
            pass
        if self._on_close:
            self._on_close()

    def reopen(self) -> None:
        """Re-show a previously hidden window — avoids creating a new Space."""
        self._closed = False
        self._cancel_auto()
        try:
            self.window.deiconify()
            self.window.attributes("-fullscreen", True)
            self.window.attributes("-topmost",    True)
            self.window.lift()
            self.window.after(150, self.window.focus_force)
        except tk.TclError:
            return
        self._avatar.start()


# mode hint text shown below state chip
_MODE_HINT: dict[str, str] = {
    "coding":   "Developer mode  ·  code context",
    "research": "Research mode  ·  searching & info",
    "phone":    "Phone mode  ·  communication",
    "meeting":  "Calendar mode  ·  scheduling",
    "focus":    "Focus mode  ·  minimal distractions",
}

"""The Holo Desk: a JARVIS-style workspace. Its panels can be grabbed and moved by hand -
point at a panel's title bar, pinch, move, open the fingers to drop - or resized by the
corner grip, and they stay where they are left.

The panels respond to ordinary mouse events, and a held pinch is an ordinary mouse
drag (the app turns drag on while this page is open), so the same code serves the hand
and a real mouse. If the hand is lost mid-drag the action controller releases the
button, so a panel is dropped where it was rather than jumping."""
from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from .colors import mix
from .system_stats import read_stats
from .ui_hud import HudRing
from .ui_pages import Page, action_label
from .ui_widgets import (
    ACCENT, AMBER, BG, BORDER, GREEN, PANEL, PANEL_ALT, RED, TEXT, TEXT_DIM, TRACK, button, card,
    gesture_glyph, gesture_label, label, resolve,
)

GRID = 8            # dropped panels snap to this grid
EASE = 0.45         # share of the remaining distance a panel covers per animation frame
FRAME_MS = 16
MIN_W, MIN_H = 200, 130
DESK_HEIGHT = 470
FOLDERS = ("Documents", "Downloads", "Desktop", "Pictures", "Music")
# Panel id -> (title, glyph, default x, y, width, height) on a desk LAYOUT_WIDTH wide; the
# default layout is stretched or squeezed sideways to the desk's real width.
LAYOUT_WIDTH = 1100
PANELS = {
    "files": ("Files", "🗂", 16, 20, 220, 300),
    "notes": ("Notes · typing test", "✎", 252, 20, 330, 220),
    "music": ("Music", "♫", 598, 20, 240, 210),
    "system": ("System Info", "◔", 854, 20, 230, 330),
    "clock": ("Clock", "◷", 252, 256, 330, 110),
}


class HoloPanel(ctk.CTkFrame):
    """A floating panel on the desk, moved by its title bar and resized by its grip."""

    def __init__(self, desk: "HoloDeskPage", panel_id: str, title: str, glyph: str) -> None:
        super().__init__(desk.desk, fg_color=PANEL, corner_radius=12, border_width=1, border_color=BORDER)
        self.pack_propagate(False)  # the panel keeps the size it is given, whatever its content
        self.page, self.panel_id = desk, panel_id
        self.pos = [0.0, 0.0]
        self.target = [0.0, 0.0]
        self.size = [MIN_W, MIN_H]
        self._grab: tuple | None = None
        self._resize: tuple | None = None
        self._animating = False

        bar = ctk.CTkFrame(self, fg_color=PANEL_ALT, corner_radius=10, height=30)
        bar.pack(fill="x", padx=3, pady=(3, 0))
        bar.pack_propagate(False)
        title_label = label(bar, f"{glyph}  {title}", 12, True, color=ACCENT)
        title_label.pack(side="left", padx=10)
        grip_hint = label(bar, "⠿", 14, color=TEXT_DIM)
        grip_hint.pack(side="right", padx=8)
        for widget in (bar, title_label, grip_hint):
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._release)
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=8, pady=(6, 2))
        grip = label(self, "◢", 14, color=TEXT_DIM, width=18, height=18)
        grip.place(relx=1.0, rely=1.0, x=-6, y=-4, anchor="se")
        grip.bind("<ButtonPress-1>", self._resize_press)
        grip.bind("<B1-Motion>", self._resize_drag)
        grip.bind("<ButtonRelease-1>", self._release)
        self.grip = grip

    def put(self, x: float, y: float, width: float, height: float) -> None:
        self.pos, self.target, self.size = [x, y], [x, y], [width, height]
        self.configure(width=int(width), height=int(height))  # CTk widgets take their size here, not in place()
        self.place(x=int(x), y=int(y))

    def _selected(self, on: bool) -> None:
        self.configure(border_width=2 if on else 1, border_color=ACCENT if on else BORDER)

    # ---------------------------------------------------------------- move
    def _press(self, event) -> None:
        self.lift()
        self._selected(True)
        self._grab = (event.x_root - self.pos[0], event.y_root - self.pos[1])

    def _drag(self, event) -> None:
        if self._grab is None:
            return
        x, y = self.page.clamp(event.x_root - self._grab[0], event.y_root - self._grab[1], *self.size)
        self.target = [x, y]
        self._animate()

    def _release(self, _event=None) -> None:
        if self._grab is None and self._resize is None:
            return
        self._grab = self._resize = None
        self._selected(False)
        x, y = self.page.clamp(round(self.target[0] / GRID) * GRID, round(self.target[1] / GRID) * GRID,
                               *self.size)
        self.target = [x, y]
        self._animate()
        self.page.save_layout()

    def _animate(self) -> None:
        """Eases the panel towards where the hand is taking it, so it glides rather than
        stepping from one camera frame to the next."""
        if self._animating:
            return
        self._animating = True

        def step() -> None:
            if not self.winfo_exists():
                return
            dx, dy = self.target[0] - self.pos[0], self.target[1] - self.pos[1]
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                self.pos = list(self.target)
                self._animating = False
            else:
                self.pos = [self.pos[0] + dx * EASE, self.pos[1] + dy * EASE]
            self.place(x=int(self.pos[0]), y=int(self.pos[1]))
            if self._animating:
                self.after(FRAME_MS, step)

        step()

    # -------------------------------------------------------------- resize
    def _resize_press(self, event) -> None:
        self.lift()
        self._selected(True)
        self._resize = (event.x_root, event.y_root, self.size[0], self.size[1])

    def _resize_drag(self, event) -> None:
        if self._resize is None:
            return
        x0, y0, w0, h0 = self._resize
        max_w, max_h = self.page.desk_size()
        width = max(MIN_W, min(max_w - self.pos[0], w0 + event.x_root - x0))
        height = max(MIN_H, min(max_h - self.pos[1], h0 + event.y_root - y0))
        self.size = [width, height]
        self.configure(width=int(width), height=int(height))


class HoloDeskPage(Page):
    title = "Holo Desk"
    subtitle = "JARVIS mode: point at a panel's title bar, pinch to grab it, move your hand, open your fingers to drop."

    def build(self) -> None:
        app = self.app
        self._built_at = time.monotonic()
        top = ctk.CTkFrame(self.body, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(4, 8))
        top.grid_columnconfigure((0, 2), weight=3, uniform="top")
        top.grid_columnconfigure(1, weight=2, uniform="top")

        camera = card(top)
        camera.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        label(camera, "◉  Camera Preview", 13, True, color=ACCENT).pack(anchor="w", padx=14, pady=(10, 4))
        self.preview_panel = ctk.CTkFrame(camera, fg_color=PANEL_ALT, corner_radius=10, height=210)
        self.preview_panel.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.preview_panel.pack_propagate(False)
        self.preview_label = ctk.CTkLabel(self.preview_panel, text="Waiting for the camera…", text_color=TEXT_DIM)
        self.preview_label.pack(fill="both", expand=True)
        self._hand = label(camera, "", 11, True)
        self._hand.pack(anchor="w", padx=14, pady=(0, 10))

        gesture = card(top)
        gesture.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        label(gesture, "Current Gesture", 13, True, color=ACCENT).pack(pady=(10, 0))
        self._ring = HudRing(gesture, size=196, thickness=9, spin=True, title_size=17)
        self._ring.pack(pady=4)
        row = ctk.CTkFrame(gesture, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))
        label(row, "Confidence", 11, color=TEXT_DIM).pack(side="left")
        self._confidence = label(row, "–", 12, True, color=ACCENT)
        self._confidence.pack(side="right")
        self._confidence_bar = ctk.CTkProgressBar(gesture, progress_color=ACCENT, fg_color=TRACK, height=6)
        self._confidence_bar.pack(fill="x", padx=16, pady=(0, 12))
        self._confidence_bar.set(0)

        controls = card(top)
        controls.grid(row=0, column=2, sticky="nsew")
        label(controls, "⚙  System Controls", 13, True, color=ACCENT).pack(anchor="w", padx=14, pady=(10, 6))
        grid = ctk.CTkFrame(controls, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="ctl")
        self._pause_tile = self._tile(grid, 0, 0, "❚❚", "Pause", app.toggle_pause)
        self._tile(grid, 0, 1, "■", "Camera", app._toggle_tracking)
        self._tile(grid, 0, 2, "⌨", "Keyboard", app.toggle_keyboard)
        self._tile(grid, 0, 3, "⚙", "Settings", lambda: app.show_page("Settings"))
        apps = list(app.config_data["apps"])[:3]
        for i, name in enumerate(apps):
            self._tile(grid, 1, i, name[:1].upper(), name[:10], lambda n=name: self._open_app(n))
        self._tile(grid, 1, len(apps), "⋯", "Add apps" if not apps else "More",
                   lambda: app.show_page("System Controls"))

        desk_card = card(self.body)
        desk_card.pack(fill="x", padx=12, pady=8)
        self.desk = tk.Canvas(desk_card, height=DESK_HEIGHT, highlightthickness=0, bd=0, bg=resolve(BG))
        self.desk.pack(fill="x", padx=2, pady=2)
        self.desk.bind("<Configure>", lambda _e: self._draw_grid())
        self.panels: dict[str, HoloPanel] = {}
        for panel_id, (title, glyph, *_frame) in PANELS.items():
            panel = HoloPanel(self, panel_id, title, glyph)
            getattr(self, f"_fill_{panel_id}")(panel.content)
            self.panels[panel_id] = panel
        self._dock()
        self.after_idle(self._draw_grid)

        label(self.body, "Hand Gestures & Actions", 15, True, color=ACCENT).pack(anchor="w", padx=24, pady=(10, 4))
        cards = ctk.CTkFrame(self.body, fg_color="transparent")
        cards.pack(fill="x", padx=12)
        mapped = [g for g in app.all_gestures() if app.action_controller.action_for(g) != "none"]
        columns = 8
        cards.grid_columnconfigure(tuple(range(columns)), weight=1, uniform="g")
        for i, gesture_name in enumerate(mapped):
            tile = card(cards)
            tile.grid(row=i // columns, column=i % columns, sticky="nsew", padx=3, pady=3)
            label(tile, gesture_glyph(gesture_name), 26).pack(pady=(8, 0))
            label(tile, gesture_label(gesture_name), 12, True, color=ACCENT).pack()
            label(tile, action_label(app.action_controller.action_for(gesture_name)), 10, color=TEXT_DIM,
                  wraplength=120).pack(pady=(0, 8))

        info = ctk.CTkFrame(self.body, fg_color="transparent")
        info.pack(fill="x", padx=12, pady=(8, 16))
        info.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="info")
        self._info(info, 0, "⤧  Move & Resize Panels",
                   "Point at a panel's title bar → pinch to select → move your hand to drag → open your "
                   "fingers to drop. Drag the ◢ corner to resize. Panels stay where you leave them.",
                   ("Reset layout", self.reset_layout))
        self._info(info, 1, "⌨  Virtual Keyboard",
                   "Type without a physical keyboard: point at a key and pinch. It types into the app you "
                   "are using; one pinch is one key.", ("Open keyboard", app.toggle_keyboard))
        self._info(info, 2, "🎙  Voice Assistant",
                   "\"Computer, open google chrome\" · \"Computer, play music\" · \"Computer, volume up\" · "
                   "\"Computer, pause control\"", ("Voice settings", lambda: app.show_page("Voice")))
        self._info(info, 3, "🛡  Safety",
                   "✓ Pause / stop control any time\n✓ Emergency stop button and gesture\n✓ Gesture "
                   "cooldowns and hold times\n✓ Tracking lost = drag released", None)
        self._stats: dict = {}
        self._stats_time = 0.0
        self._refresh()

    # ---------------------------------------------------------------- pieces
    def _tile(self, parent, row: int, column: int, glyph: str, text: str, command) -> ctk.CTkButton:
        tile = ctk.CTkButton(parent, text=f"{glyph}\n{text}", width=70, height=66, corner_radius=12,
                             fg_color=PANEL_ALT, hover_color=BORDER, border_width=1, border_color=BORDER,
                             text_color=TEXT, font=ctk.CTkFont(size=12, weight="bold"), command=command)
        tile.grid(row=row, column=column, padx=4, pady=4, sticky="ew")
        return tile

    def _info(self, parent, column: int, title: str, text: str, action) -> None:
        box = card(parent)
        box.grid(row=0, column=column, sticky="nsew", padx=3)
        label(box, title, 13, True, color=ACCENT).pack(anchor="w", padx=14, pady=(10, 4))
        label(box, text, 11, color=TEXT_DIM, justify="left", wraplength=230).pack(anchor="w", padx=14)
        if action:
            button(box, action[0], action[1], "ghost", 130).pack(anchor="w", padx=14, pady=(8, 12))
        else:
            ctk.CTkFrame(box, fg_color="transparent", height=10).pack()

    def _dock(self) -> None:
        dock = ctk.CTkFrame(self.desk, fg_color=PANEL, corner_radius=18, border_width=1, border_color=BORDER)
        items = [(name[:1].upper(), name, lambda n=name: self._open_app(n)) for name in self.app.config_data["apps"]]
        items += [("◀", "Previous desktop", lambda: self._run("prev_workspace")),
                  ("▦", "Mission Control", lambda: self._run("mission_control")),
                  ("▶", "Next desktop", lambda: self._run("next_workspace")),
                  ("⌨", "Keyboard", self.app.toggle_keyboard)]
        for glyph, _tip, command in items[:9]:
            ctk.CTkButton(dock, text=glyph, width=44, height=44, corner_radius=12, fg_color=PANEL_ALT,
                          hover_color=BORDER, text_color=ACCENT, font=ctk.CTkFont(size=18, weight="bold"),
                          command=command).pack(side="left", padx=4, pady=6)
        dock.place(relx=0.5, rely=1.0, y=-12, anchor="s")
        self._dock_frame = dock

    def _fill_files(self, parent) -> None:
        for folder in FOLDERS:
            ctk.CTkButton(parent, text=f"📁  {folder}", anchor="w", height=34, corner_radius=8, fg_color=PANEL_ALT,
                          hover_color=BORDER, text_color=TEXT, font=ctk.CTkFont(size=12, weight="bold"),
                          command=lambda f=folder: self._open_folder(f)).pack(fill="x", pady=3)

    def _fill_notes(self, parent) -> None:
        self._notes = ctk.CTkTextbox(parent, fg_color=PANEL_ALT, border_width=1, border_color=BORDER,
                                     text_color=TEXT, font=ctk.CTkFont(size=13), wrap="word")
        self._notes.pack(fill="both", expand=True, pady=(0, 14))
        self._notes.insert("1.0", self.app.config_data["holo_notes"])
        self._notes.bind("<KeyRelease>", lambda _e: self._save_notes())

    def _fill_music(self, parent) -> None:
        label(parent, "Controls whatever is playing", 11, color=TEXT_DIM).pack(anchor="w")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(pady=8)
        for glyph, action in (("⏮", "prev_track"), ("⏯", "play_pause"), ("⏭", "next_track")):
            ctk.CTkButton(row, text=glyph, width=54, height=46, corner_radius=23, fg_color=PANEL_ALT,
                          hover_color=BORDER, text_color=ACCENT, font=ctk.CTkFont(size=20),
                          command=lambda a=action: self._run(a)).pack(side="left", padx=4)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack()
        for glyph, action in (("🔉", "volume_down"), ("🔇", "mute"), ("🔊", "volume_up")):
            ctk.CTkButton(row, text=glyph, width=54, height=32, corner_radius=8, fg_color=PANEL_ALT,
                          hover_color=BORDER, text_color=TEXT, command=lambda a=action: self._run(a),
                          ).pack(side="left", padx=4)

    def _fill_system(self, parent) -> None:
        self._gauges = {}
        for key, title in (("cpu", "CPU"), ("memory", "Memory"), ("battery", "Battery")):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ring = HudRing(row, size=76, thickness=6, title_size=13)
            ring.pack(side="left")
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", padx=10)
            label(text, title, 11, color=TEXT_DIM).pack(anchor="w")
            value = label(text, "…", 18, True)
            value.pack(anchor="w")
            self._gauges[key] = (ring, value)

    def _fill_clock(self, parent) -> None:
        self._time = label(parent, "", 30, True, color=ACCENT)
        self._time.pack(side="left", padx=6)
        self._date = label(parent, "", 12, color=TEXT_DIM, justify="left")
        self._date.pack(side="left", padx=10)

    # ---------------------------------------------------------------- actions
    def _run(self, action: str) -> None:
        self.app.action_controller.run_action(action, force=True)

    def _open_app(self, name: str) -> None:
        self.app.action_controller.run_action("app:" + name, force=True)

    @staticmethod
    def _open_folder(folder: str) -> None:
        path = Path.home() / folder  # a fixed list of the user's own folders, nothing else
        if not path.is_dir():
            return
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _save_notes(self) -> None:
        self.app.config_data["holo_notes"] = self._notes.get("1.0", "end-1c")[:5000]
        self.app._schedule_save()

    # ---------------------------------------------------------------- layout
    def desk_size(self) -> tuple[float, float]:
        return max(400, self.desk.winfo_width()), DESK_HEIGHT

    def clamp(self, x: float, y: float, width: float, height: float) -> tuple[float, float]:
        max_w, max_h = self.desk_size()
        return max(0.0, min(max_w - width, x)), max(0.0, min(max_h - height, y))

    def save_layout(self) -> None:
        self.app.config_data["holo_panels"] = {
            pid: [int(p.target[0]), int(p.target[1]), int(p.size[0]), int(p.size[1])] for pid, p in self.panels.items()}
        self.app._schedule_save()

    def default_frame(self, panel_id: str) -> list[float]:
        _title, _glyph, x, y, w, h = PANELS[panel_id]
        scale = max(0.6, min(1.3, self.desk_size()[0] / LAYOUT_WIDTH))
        return [x * scale, y, w * min(1.0, scale), h]

    def _place_panels(self) -> None:
        saved = self.app.config_data["holo_panels"]
        for panel_id, panel in self.panels.items():
            x, y, w, h = saved.get(panel_id) or self.default_frame(panel_id)
            panel.put(*self.clamp(x, y, w, h), w, h)

    def reset_layout(self) -> None:
        for pid, panel in self.panels.items():
            x, y, w, h = self.default_frame(pid)
            panel.size = [w, h]
            panel.configure(width=int(w), height=int(h))
            panel.target = list(self.clamp(x, y, w, h))
            panel._animate()
        self.app.config_data["holo_panels"] = {}
        self.app._schedule_save()

    def _draw_grid(self) -> None:
        """A faint holographic grid behind the panels."""
        desk = self.desk
        desk.delete("grid")
        bg = resolve(BG)
        desk.configure(bg=bg)
        line = mix(resolve(ACCENT), bg, 0.9)
        width, height = desk.winfo_width(), desk.winfo_height()
        for x in range(0, width, 40):
            desk.create_line(x, 0, x, height, fill=line, tags="grid")
        for y in range(0, height, 40):
            desk.create_line(0, y, width, y, fill=line, tags="grid")
        desk.tag_lower("grid")
        if len(self.panels) < len(PANELS) or width <= 1:
            return  # still being built
        if not self.app.config_data["holo_panels"]:
            self._place_panels()  # the default layout, fitted to the desk's width
            return
        for panel in self.panels.values():  # keep panels inside a desk that got narrower
            x, y = self.clamp(panel.target[0], panel.target[1], *panel.size)
            if (x, y) != tuple(panel.target):
                panel.put(x, y, *panel.size)

    # ---------------------------------------------------------------- live
    def _refresh(self) -> None:
        if not self._ring.winfo_exists():
            return
        if self.winfo_ismapped():
            app = self.app
            hand = app.last_event
            if not app.camera_running:
                self._ring.set(0, "Camera off", "Start Camera", colour=AMBER)
                self._hand.configure(text="● Camera off", text_color=AMBER)
            elif app.safety.paused:
                self._ring.set(0, "Paused", "Control stopped", colour=RED)
            elif hand is None or hand.name in ("none", "resting"):
                self._ring.set(0, "No hand", "Show your hand", colour=None)
            else:
                action = action_label(app.action_controller.action_for(hand.name))
                self._ring.set(hand.confidence / 100.0, gesture_label(hand.name), action[:22])
            if app.camera_running:
                seen = hand is not None and hand.name not in ("none", "resting")
                self._hand.configure(text="✓ Hand detected · 21 landmarks" if seen else "● Looking for a hand…",
                                     text_color=GREEN if seen else TEXT_DIM)
            confidence = hand.confidence if hand is not None and hand.name not in ("none", "resting") else 0
            self._confidence.configure(text=f"{confidence:.0f}%")
            self._confidence_bar.set(confidence / 100.0)
            self._pause_tile.configure(text="▶\nResume" if app.safety.paused else "❚❚\nPause",
                                       text_color=RED if app.safety.paused else TEXT)
            self._time.configure(text=time.strftime("%H:%M:%S"))
            self._date.configure(text=time.strftime("%A\n%d %B %Y"))
            now = time.monotonic()
            if now - self._stats_time > 3.0:
                self._stats_time = now
                threading.Thread(target=self._read_stats, daemon=True).start()
            self._show_stats()
        self.after(150, self._refresh)

    def _read_stats(self) -> None:
        self._stats = read_stats()  # a plain dict swap: safe to read from the UI thread

    def _show_stats(self) -> None:
        stats = self._stats
        for key, (ring, value) in self._gauges.items():
            reading = stats.get(key)
            if key == "battery" and reading is not None:
                percent, charging = reading
                ring.set(percent / 100.0, f"{percent:.0f}%", colour=GREEN if charging else (
                    RED if percent < 20 else None))
                value.configure(text=f"{percent:.0f}%" + (" ⚡" if charging else ""))
            elif reading is not None:
                ring.set(reading / 100.0, f"{reading:.0f}%", colour=RED if reading > 85 else None)
                value.configure(text=f"{reading:.0f}%")
            else:
                ring.set(0, "–")
                value.configure(text="n/a")

    def on_show(self) -> None:
        self.rebuild()  # apps, mappings or the theme may have changed

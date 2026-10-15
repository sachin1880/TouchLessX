"""The virtual keyboard window: always on top, with the key under the cursor lit up.

With the hand, a pinch over a key never reaches this window as a click - the app types
the key straight into the application in front (see `App._keyboard_click`), so
TouchlessX doesn't take the focus away. With a real mouse the click does land here and
brings TouchlessX forward, so the window hands the focus back to the application that
had it before typing."""
from __future__ import annotations

import os
import platform
import time

import customtkinter as ctk

from .ui_widgets import ACCENT, BORDER, GREEN, ON_ACCENT, PANEL, PANEL_ALT, TEXT, TEXT_DIM, button, label
from .virtual_keyboard import KEY_ROWS, SHIFT, key_label

KEY_SIZE, KEY_GAP = 54, 6
REFRESH_MS = 60


class KeyboardWindow(ctk.CTkToplevel):
    def __init__(self, app) -> None:
        super().__init__(app, fg_color=PANEL)
        self.app = app
        self.model = app.keyboard_model
        self.title("TouchlessX Keyboard")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", app.toggle_keyboard)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        label(top, "⌨", 18, color=ACCENT).pack(side="left")
        self.preview = label(top, "Point at a key and pinch to type", 13, True, anchor="w")
        self.preview.pack(side="left", fill="x", expand=True, padx=8)
        button(top, "Hide", app.toggle_keyboard, "ghost", 64).pack(side="right")

        self.keys: dict[str, ctk.CTkButton] = {}
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(padx=12, pady=(4, 12))
        for row in KEY_ROWS:
            line = ctk.CTkFrame(grid, fg_color="transparent")
            line.pack(pady=KEY_GAP // 2)
            for key in row:
                width = KEY_SIZE * 6 if key == "space" else (KEY_SIZE + 20 if key in ("⌫", "⏎", SHIFT) else KEY_SIZE)
                widget = ctk.CTkButton(
                    line, text=key_label(key), width=width, height=KEY_SIZE, corner_radius=10,
                    fg_color=PANEL_ALT, hover_color=PANEL_ALT, text_color=TEXT, border_width=1,
                    border_color=BORDER, font=ctk.CTkFont(size=18, weight="bold"),
                    command=lambda k=key: self._mouse_press(k))
                widget.pack(side="left", padx=KEY_GAP // 2)
                self.keys[key] = widget
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, self.winfo_screenheight() - height - 90)
        self.geometry(f"+{x}+{y}")
        self._hover: str | None = None
        self._flash_until: dict[str, float] = {}
        self._previous_app = None
        self._shown_shift = None
        self._refresh()

    def _refresh(self) -> None:
        """Publishes where the keys are on screen (for the tracking thread) and lights up
        the key under the cursor."""
        if not self.winfo_exists():
            return
        if self.winfo_viewable():
            rects = [(key, w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())
                     for key, w in self.keys.items()]
            self.model.set_layout((self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()),
                                  rects)
            x, y = self.winfo_pointerxy()
            hover = self.model.key_at(x, y)
            now = time.monotonic()
            for key, widget in self.keys.items():
                if self._flash_until.get(key, 0.0) > now:
                    colour, border = GREEN, GREEN
                elif key == SHIFT and self.model.shift:
                    colour, border = ACCENT, ACCENT
                else:
                    colour, border = PANEL_ALT, ACCENT if key == hover else BORDER
                if widget.cget("fg_color") != colour or widget.cget("border_color") != border:
                    widget.configure(fg_color=colour, hover_color=colour, border_color=border,
                                     border_width=2 if key == hover else 1,
                                     text_color=ON_ACCENT if colour in (GREEN, ACCENT) else TEXT)
            if self._shown_shift != self.model.shift:
                self._shown_shift = self.model.shift
                for key, widget in self.keys.items():
                    widget.configure(text=key_label(key, self.model.shift))
            typed = self.model.typed[-40:]
            self.preview.configure(text=typed if typed else "Point at a key and pinch to type",
                                   text_color=TEXT if typed else TEXT_DIM)
            self._remember_front_app()
        self.after(REFRESH_MS, self._refresh)

    def flash(self, key: str) -> None:
        self._flash_until[key] = time.monotonic() + 0.18

    def _remember_front_app(self) -> None:
        if platform.system() != "Darwin":
            return
        try:
            from AppKit import NSWorkspace

            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is not None and front.processIdentifier() != os.getpid():
                self._previous_app = front
        except Exception:
            pass

    def _mouse_press(self, key: str) -> None:
        """A real mouse click on a key: give the focus back, then type."""
        if self._previous_app is not None:
            try:
                self._previous_app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
            except Exception:
                pass
            self.after(120, lambda: self.app.keyboard_type(key))
        else:
            self.app.keyboard_type(key)

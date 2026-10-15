"""The virtual keyboard's logic: layout, Shift, the repeat guard and finding the key
under the cursor. Kept free of Tk so it can be tested; the window is ui_keyboard.py.

A pinch over the keyboard never reaches the keyboard window as a real click: the app
asks `key_at` which key is under the cursor and types it straight into the application
that has focus. That is what lets the keyboard type into other apps - a real click
would bring TouchlessX to the front and the letters would land in TouchlessX instead."""
from __future__ import annotations

import threading

KEY_ROWS = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "⌫"],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "-"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", "'", "⏎"],
    ["⇧", "z", "x", "c", "v", "b", "n", "m", ",", ".", "?"],
    ["@", "space", "!"],
]
# Keys that are pressed rather than typed: key -> pyautogui key name.
SPECIAL_KEYS = {"⌫": "backspace", "⏎": "enter", "space": "space"}
SHIFT = "⇧"
SHIFTED = {"1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
           "-": "_", "'": '"', ",": ";", ".": ":", "?": "/"}
REPEAT_GUARD_SECONDS = 0.3  # one pinch, one key: a second press this soon is ignored


def key_label(key: str, shift: bool = False) -> str:
    if key == "space":
        return "space"
    if shift:
        return key.upper() if key.isalpha() else SHIFTED.get(key, key)
    return key


class KeyboardModel:
    def __init__(self) -> None:
        self.shift = False
        self.visible = False
        self.typed = ""                       # the last characters typed, for the preview line
        self._lock = threading.Lock()         # rects are written by the UI thread, read by the tracking thread
        self._area: tuple[float, float, float, float] | None = None
        self._rects: list[tuple[str, float, float, float, float]] = []
        self._last_press = -1e9

    def set_layout(self, area: tuple[float, float, float, float] | None,
                   rects: list[tuple[str, float, float, float, float]]) -> None:
        """The keyboard window's screen area and each key's (key, x, y, width, height)."""
        with self._lock:
            self._area, self._rects = area, list(rects)

    def contains(self, x: float, y: float) -> bool:
        with self._lock:
            area = self._area
        if not self.visible or area is None:
            return False
        ax, ay, aw, ah = area
        return ax <= x < ax + aw and ay <= y < ay + ah

    def key_at(self, x: float, y: float) -> str | None:
        with self._lock:
            rects = self._rects
        for key, kx, ky, kw, kh in rects:
            if kx <= x < kx + kw and ky <= y < ky + kh:
                return key
        return None

    def press(self, key: str, now: float) -> tuple[str, str] | None:
        """What pressing `key` does: ("text", characters), ("key", key name) or
        ("shift", ""). None when it comes too soon after the previous press."""
        if now - self._last_press < REPEAT_GUARD_SECONDS:
            return None
        self._last_press = now
        if key == SHIFT:
            self.shift = not self.shift
            return "shift", ""
        if key in SPECIAL_KEYS:
            if key == "⌫":
                self.typed = self.typed[:-1]
            elif key == "space":
                self.typed += " "
            return "key", SPECIAL_KEYS[key]
        text = key_label(key, self.shift)
        self.shift = False  # Shift applies to one key, like on a phone
        self.typed = (self.typed + text)[-60:]
        return "text", text

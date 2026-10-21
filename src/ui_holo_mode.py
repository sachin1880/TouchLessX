"""The full-screen Holo mode window. It owns a HoloScene (holo_scene.py) and is the
bridge between the tracking thread and the screen:

* the tracking thread calls `feed()` with each frame and both hands; while Holo mode is
  open the app sends gestures here instead of to the action controller, so the mouse is
  never moved and nothing is clicked behind the hologram;
* the UI thread runs `_loop()`: hands -> scene.handle() -> actions -> scene.render() ->
  one image on a full-screen canvas.

Leave with Esc, the EXIT button, or both open palms held for a moment.

Real-world actions available inside Holo mode:
  • COMPUTER panel — pinch any button to act on the desktop without leaving (screenshot,
    switch app, copy/paste, undo, zoom, close tab, new tab)
  • SMART HOME panel — pinch a device button to fire its webhook
  • JARVIS panel — shows the last voice command and AI/action response live
  • MEDIA panel — play/pause, next, volume (already wired)
  • HOLOGRAM panel — swap the 3D model, sound, trails, reset
"""
from __future__ import annotations

import platform
import threading
import time
import tkinter as tk

import cv2
from PIL import Image, ImageTk

from .holo_scene import Hand, HandInput, HoloScene
from .holo_sounds import SoundBoard
from .system_stats import read_stats

FRAME_MS = 15
IDLE_RENDER_S = 1 / 30     # without new camera frames the hologram still moves
STATS_EVERY_S = 2.0


class HoloMode:
    def __init__(self, app) -> None:
        self.app = app
        self.window = tk.Toplevel(app)
        self.window.configure(bg="black")
        self.window.title("TouchlessX Holo")
        width, height = app.winfo_screenwidth(), app.winfo_screenheight()
        self.window.geometry(f"{width}x{height}+0+0")
        self.window.attributes("-fullscreen", True)
        self.canvas = tk.Canvas(self.window, width=width, height=height, bg="black", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        devices = app.smart_home.names() if getattr(app, "smart_home", None) is not None else []
        self.scene = HoloScene(width, height, list(app.config_data["apps"]),
                               app.config_data["holo_full_layout"], smart_home_devices=devices)
        self.hands = HandInput(width, height, app.gesture_engine.pinch_threshold)  # the user's own pinch setting
        self._photo = ImageTk.PhotoImage(Image.new("RGB", (width, height)))
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self._lock = threading.Lock()
        self._latest = None           # (half-size frame, main hand, other hand, gesture name, time)
        self._frame = None
        self._last_render = 0.0
        self._last_frame_at = 0.0
        self._hand = Hand()
        self.sounds = SoundBoard()
        self._closed = False
        self._last_voice: tuple[str, str] = ("", "")   # (heard, result) last pushed to JARVIS panel
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(100, self.window.focus_force)
        threading.Thread(target=self._stats_loop, daemon=True, name="holo-stats").start()
        self._loop()

    # ------------------------------------------------------------- tracking thread
    def feed(self, frame, result, event, started: float) -> None:
        """Called on the tracking thread for every frame. Keeps only the newest."""
        small = cv2.resize(frame, self.scene._half, interpolation=cv2.INTER_AREA) if frame is not None else None
        main = result.landmarks.copy() if result is not None else None
        other = getattr(result, "other", None)
        other = other.landmarks.copy() if other is not None else None
        with self._lock:
            self._latest = (small, main, other, getattr(event, "name", ""), started)

    # ------------------------------------------------------------- UI thread
    def _loop(self) -> None:
        if self._closed:
            return
        now = time.monotonic()
        with self._lock:
            latest, self._latest = self._latest, None
        if latest is not None:
            self._frame, main, other, gesture, _t = latest
            self._last_frame_at = now
            hand = self._hand = self.hands.update(main, now, other)
            self.scene.notice = self._notice(now)
            for action in self.scene.handle(hand, now, gesture):
                self._perform(action)
                if self._closed:
                    return
        elif now - self._last_render >= IDLE_RENDER_S:
            # Between camera frames only time moves on (spin, explosion); hand input comes
            # from real frames alone, or repeated samples would read as a still hand.
            self.scene.idle(now)
            self.scene.notice = self._notice(now)
            hand = self._hand if now - self._last_frame_at < 0.3 else Hand()
        else:
            hand = None
        # Forward voice/AI state to the JARVIS panel whenever it changes.
        heard = getattr(self.app, "voice_heard", "")
        result = getattr(self.app, "voice_result", "")
        current_voice = (heard, result)
        if current_voice != self._last_voice and (heard or result):
            self._last_voice = current_voice
            self.scene.set_jarvis(heard, result)
        if hand is not None:
            self._photo.paste(Image.fromarray(self.scene.render(self._frame, hand, now)))
            self._last_render = now
        for name in self.scene.sounds:
            self.sounds.play(name)
        self.scene.sounds.clear()
        self.window.after(FRAME_MS, self._loop)

    def _notice(self, now: float) -> str:
        if getattr(self.app, "_face_mode", False):
            return "Face Control is on - turn it off to use your hands here"
        if not self.app._camera_running or now - self._last_frame_at > 1.5:
            return "Starting the camera…" if self.app._tracking_wanted.is_set() else "The camera is off"
        return ""

    # Actions the COMPUTER panel maps to action_controller action IDs.
    _QUICK_ACTIONS = {
        "switch_app": "switch_app", "new_tab": "new_tab", "close_tab": "close_tab",
        "copy": "copy", "paste": "paste", "undo": "undo", "redo": "redo",
        "zoom_in": "zoom_in", "zoom_out": "zoom_out", "mission": "mission_control",
        "scroll_up": "scroll_up", "scroll_down": "scroll_down",
    }

    def _perform(self, action: str) -> None:
        kind, _, value = action.partition(":")
        if action == "exit":
            self.close()
        elif kind == "media":
            self.app.action_controller.run_action(value, force=True)
        elif kind == "app":
            self.close()  # the app opens on the normal desktop, so step out of the hologram
            self.app.action_controller.run_action(action, force=True)
        elif kind == "quick":
            # Execute a desktop action without leaving Holo mode.
            mapped = self._QUICK_ACTIONS.get(value)
            if mapped:
                self.app.action_controller.run_action(mapped, force=True)
            elif value == "screenshot":
                # Not in action_controller: fire the OS screenshot key combo directly.
                if platform.system() == "Darwin":
                    self.app.executor.hotkey(["command", "shift", "3"])
                else:
                    self.app.executor.hotkey(["win", "shift", "s"])
        elif kind == "smart_home":
            # Trigger a smart home device webhook by device name.
            if getattr(self.app, "smart_home", None) is not None:
                self.app.smart_home.trigger(value)
        elif kind == "cctv":
            callback = self.app.action_controller.callbacks.get("cctv")
            if callback is not None:
                callback(value)

    def _stats_loop(self) -> None:
        while not self._closed:
            try:
                self.scene.stats = read_stats()
            except Exception:
                pass
            time.sleep(STATS_EVERY_S)

    # ------------------------------------------------------------- closing
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.sounds.close()
        self.app.holo_mode_closed(self.scene.layout() if self.scene.layout_changed else None)
        try:
            self.window.attributes("-fullscreen", False)
            self.window.destroy()
        except tk.TclError:
            pass

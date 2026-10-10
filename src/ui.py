"""User Interface: shows video, landmarks, recognized gesture, settings, and
enable/disable status. The camera/tracking/gesture pipeline runs on a background
thread; Tkinter widgets are only ever touched from the main thread, via a queue.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path

import customtkinter as ctk
import cv2
from PIL import Image

from .action_controller import ActionController, NullExecutor, PyAutoGuiExecutor
from .camera import Camera, CameraError
from .gesture_engine import GestureEngine
from .hand_tracker import HandTracker
from .safety import SafetyGuard

BG = "#0a0e14"
PANEL = "#121a2b"
PANEL_ALT = "#0f1624"
ACCENT = "#3d9bff"
GREEN = "#31d488"
RED = "#ff5a5f"
TEXT = "#e9ecef"
TEXT_DIM = "#8b96a8"

GESTURE_LABELS = {
    "open_palm": "Open Palm",
    "fist": "Fist",
    "pinch": "Pinch",
    "two_fingers": "Two Fingers",
    "point": "Point",
    "swipe_left": "Swipe Left",
    "swipe_right": "Swipe Right",
    "unknown": "Unknown",
    "none": "No Hand",
}

# (config key, display label, functional-in-this-version)
CATEGORY_CARDS = [
    ("mouse_control", "Mouse Control", True),
    ("media_control", "Media Control", True),
    ("presentation", "Presentation", True),
    ("volume_control", "Volume Control", True),
    ("smart_home", "Smart Home", False),
    ("ai_assistant", "AI Assistant", False),
]

NAV_ITEMS = [
    "Dashboard", "Gesture Settings", "System Controls",
    "Media & Presentation", "Smart Home", "AI Assistant", "Settings",
]


class App(ctk.CTk):
    def __init__(self, config: dict, config_path: Path) -> None:
        super().__init__()
        self.config_data = config
        self.config_path = config_path

        ctk.set_appearance_mode("dark")
        self.title("TouchlessX — Control Your World with Hand Gestures")
        self.geometry("1320x840")
        self.minsize(1100, 720)
        self.configure(fg_color=BG)

        sens = config.get("sensitivity", {})
        self.gesture_engine = GestureEngine(
            pinch_threshold=sens.get("pinch_threshold", 0.4),
            swipe_min_distance=sens.get("swipe_min_distance", 0.15),
            swipe_max_duration=sens.get("swipe_max_duration", 0.6),
            cooldown_seconds=sens.get("cooldown_seconds", 0.6),
        )
        self.safety = SafetyGuard(confidence_threshold=sens.get("confidence_threshold", 0.6))

        self._input_unavailable = False
        try:
            executor = PyAutoGuiExecutor()
        except Exception:
            executor = NullExecutor()
            self._input_unavailable = True

        self.action_controller = ActionController(
            executor=executor,
            safety=self.safety,
            enabled=dict(config.get("enabled", {})),
            cursor_smoothing=sens.get("cursor_smoothing", 0.5),
        )

        self.camera = Camera(
            index=config.get("camera_index", 0),
            width=config.get("frame_width", 960),
            height=config.get("frame_height", 540),
        )
        self.tracker = HandTracker()

        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._stop_event = threading.Event()
        self._recent_actions: deque = deque(maxlen=20)
        self._confidence_history: deque = deque(maxlen=60)
        self._total_gestures = 0
        self._session_start = time.monotonic()
        self._toggle_vars: dict[str, ctk.BooleanVar] = {}

        self._build_layout()
        if self._input_unavailable:
            self._log_system_note(
                "Input control unavailable (preview-only mode) - "
                "check OS accessibility/input permissions for this app."
            )

        self._worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<space>", lambda _e: self._toggle_pause())
        self.after(30, self._poll_queue)
        self.after(1000, self._tick_session_stats)

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color=BG)
        self.content.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(0, 16))
        self.content.grid_columnconfigure(0, weight=3)
        self.content.grid_columnconfigure(1, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._build_dashboard(self.content)
        self._build_right_panel(self.content)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=PANEL, height=64, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_propagate(False)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left", padx=20, pady=8)
        ctk.CTkLabel(brand, text="TouchlessX", font=ctk.CTkFont(size=19, weight="bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(brand, text="Control Your World with Hand Gestures", font=ctk.CTkFont(size=11),
                     text_color=TEXT_DIM).pack(anchor="w")

        status = ctk.CTkFrame(header, fg_color="transparent")
        status.pack(side="right", padx=20)
        self.camera_status_label = ctk.CTkLabel(
            status, text="● Starting camera…", font=ctk.CTkFont(size=12), text_color=TEXT_DIM
        )
        self.camera_status_label.pack(side="left", padx=12)
        self.fps_label = ctk.CTkLabel(status, text="FPS --", font=ctk.CTkFont(size=12, weight="bold"),
                                       text_color=TEXT, fg_color=PANEL_ALT, corner_radius=8)
        self.fps_label.pack(side="left", ipadx=10, ipady=4)
        self.pause_button = ctk.CTkButton(status, text="Pause", width=90, fg_color=ACCENT,
                                           hover_color="#2d7fd6", command=self._toggle_pause)
        self.pause_button.pack(side="left", padx=(12, 0))

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=PANEL, width=220, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        for i, item in enumerate(NAV_ITEMS):
            selected = item == "Dashboard"
            btn = ctk.CTkButton(
                sidebar, text=item, anchor="w",
                fg_color=(ACCENT if selected else "transparent"),
                hover_color=("#2d7fd6" if selected else PANEL_ALT),
                text_color=TEXT if selected else TEXT_DIM,
                corner_radius=10, height=40,
                command=lambda name=item: self._on_nav(name),
            )
            btn.pack(fill="x", padx=14, pady=(14 if i == 0 else 4, 4))

        tip = ctk.CTkFrame(sidebar, fg_color=PANEL_ALT, corner_radius=14)
        tip.pack(fill="x", padx=14, pady=20, side="bottom")
        ctk.CTkLabel(tip, text="Smarter Gestures,\nBetter Control", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=TEXT, justify="left").pack(anchor="w", padx=14, pady=(14, 4))
        ctk.CTkLabel(tip, text="On-device gesture recognition\nfor a touchless desktop.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM, justify="left",
                     wraplength=180).pack(anchor="w", padx=14, pady=(0, 14))

    def _on_nav(self, name: str) -> None:
        if name == "Dashboard":
            self.content.tkraise()
            return
        if not hasattr(self, "_placeholder"):
            self._placeholder = ctk.CTkFrame(self, fg_color=BG)
            self._placeholder.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(0, 16))
            self._placeholder_label = ctk.CTkLabel(self._placeholder, text="", font=ctk.CTkFont(size=16),
                                                     text_color=TEXT_DIM, justify="center")
            self._placeholder_label.place(relx=0.5, rely=0.45, anchor="center")
        self._placeholder_label.configure(text=f"{name}\n\nComing in a future phase of TouchlessX.")
        self._placeholder.tkraise()

    # ------------------------------------------------------------- dashboard
    def _build_dashboard(self, parent) -> None:
        left = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)

        video_panel = ctk.CTkFrame(left, fg_color=PANEL, corner_radius=16)
        video_panel.pack(fill="both", expand=True)
        self.video_label = ctk.CTkLabel(video_panel, text="Starting camera…", text_color=TEXT_DIM)
        self.video_label.pack(fill="both", expand=True, padx=4, pady=4)

        cards = ctk.CTkFrame(left, fg_color="transparent")
        cards.pack(fill="x", pady=(12, 0))
        cards.grid_columnconfigure((0, 1, 2), weight=1)
        self.gesture_card = self._info_card(cards, "Current Gesture", "—", 0)
        self.confidence_card = self._info_card(cards, "Confidence", "0%", 1)
        self.action_card = self._info_card(cards, "Last Action", "No action", 2)

    def _info_card(self, parent, title: str, value: str, col: int) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=14)
        card.grid(row=0, column=col, sticky="nsew", padx=6)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(
            anchor="w", padx=16, pady=(12, 0))
        value_label = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=20, weight="bold"), text_color=TEXT)
        value_label.pack(anchor="w", padx=16, pady=(0, 14))
        return value_label

    # ----------------------------------------------------------- right panel
    def _build_right_panel(self, parent) -> None:
        right = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)

        ctk.CTkLabel(right, text="Quick Controls", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).pack(anchor="w", pady=(0, 10))
        grid = ctk.CTkFrame(right, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1)
        for i, (key, label, functional) in enumerate(CATEGORY_CARDS):
            self._build_toggle_card(grid, key, label, functional, row=i // 2, col=i % 2)

        ctk.CTkLabel(right, text="Recent Gestures", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).pack(anchor="w", pady=(20, 10))
        self.recent_list = ctk.CTkFrame(right, fg_color=PANEL, corner_radius=14)
        self.recent_list.pack(fill="x")
        self._recent_empty = ctk.CTkLabel(self.recent_list, text="No gestures yet.", text_color=TEXT_DIM)
        self._recent_empty.pack(padx=14, pady=14, anchor="w")

        ctk.CTkLabel(right, text="System Status", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).pack(anchor="w", pady=(20, 10))
        status_card = ctk.CTkFrame(right, fg_color=PANEL, corner_radius=14)
        status_card.pack(fill="x")
        self.status_rows: dict[str, ctk.CTkLabel] = {}
        for key in ("Camera", "Hand Tracking", "Safety"):
            row = ctk.CTkFrame(status_card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=6)
            ctk.CTkLabel(row, text=key, text_color=TEXT_DIM, font=ctk.CTkFont(size=12)).pack(side="left")
            val = ctk.CTkLabel(row, text="—", text_color=TEXT_DIM, font=ctk.CTkFont(size=12, weight="bold"))
            val.pack(side="right")
            self.status_rows[key] = val

        ctk.CTkLabel(right, text="Session Stats", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).pack(anchor="w", pady=(20, 10))
        stats_card = ctk.CTkFrame(right, fg_color=PANEL, corner_radius=14)
        stats_card.pack(fill="x", pady=(0, 10))
        self.stats_label = ctk.CTkLabel(stats_card, text="", justify="left", text_color=TEXT,
                                         font=ctk.CTkFont(size=12))
        self.stats_label.pack(anchor="w", padx=14, pady=14)
        self.sparkline = tk.Canvas(stats_card, height=50, bg=PANEL, highlightthickness=0)
        self.sparkline.pack(fill="x", padx=14, pady=(0, 14))

    def _build_toggle_card(self, parent, key: str, label: str, functional: bool, row: int, col: int) -> None:
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
        ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT).pack(
            anchor="w", padx=12, pady=(10, 2))
        if functional:
            enabled_now = self.config_data.get("enabled", {}).get(key, True)
            var = ctk.BooleanVar(value=enabled_now)
            switch = ctk.CTkSwitch(card, text="Enabled" if enabled_now else "Disabled", variable=var)
            switch.configure(command=lambda k=key, v=var, sw=switch: self._on_toggle(k, v, sw))
            switch.pack(anchor="w", padx=12, pady=(0, 10))
            self._toggle_vars[key] = var
        else:
            ctk.CTkLabel(card, text="Coming soon", font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(
                anchor="w", padx=12, pady=(0, 10))

    def _on_toggle(self, key: str, var: ctk.BooleanVar, switch: ctk.CTkSwitch) -> None:
        value = var.get()
        self.action_controller.enabled[key] = value
        self.config_data.setdefault("enabled", {})[key] = value
        switch.configure(text="Enabled" if value else "Disabled")
        self._save_config()

    def _save_config(self) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=2)
        except OSError:
            pass  # Non-critical - toggles just won't persist across restarts.

    # --------------------------------------------------------------- pause
    def _toggle_pause(self) -> None:
        paused = self.safety.toggle_pause()
        self.pause_button.configure(text="Resume" if paused else "Pause", fg_color=RED if paused else ACCENT)

    def _log_system_note(self, text: str) -> None:
        self._recent_empty.configure(text=text)

    # ----------------------------------------------------------- background
    def _capture_loop(self) -> None:
        try:
            self.camera.start()
        except CameraError as exc:
            self._frame_queue.put(("error", str(exc)))
            return

        frame_times: deque = deque(maxlen=20)
        while not self._stop_event.is_set():
            frame = self.camera.read()
            if frame is None:
                continue
            now = time.monotonic()
            frame_times.append(now)

            result = self.tracker.process(frame)
            if result is not None:
                self.tracker.draw(frame, result)

            event = self.gesture_engine.update(result, now)
            dispatch = self.action_controller.dispatch(event)

            fps = 0.0
            if len(frame_times) >= 2:
                fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0] + 1e-6)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            self._push_frame(("frame", image, event, dispatch, fps))

        self.camera.stop()
        self.tracker.close()

    def _push_frame(self, payload: tuple) -> None:
        """Keeps only the newest frame queued - a dropped frame is fine, a growing
        backlog that makes the UI lag behind real hand movement is not."""
        try:
            self._frame_queue.put_nowait(payload)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(payload)
            except queue.Full:
                pass

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, *rest = self._frame_queue.get_nowait()
                if kind == "error":
                    self._on_camera_error(rest[0])
                elif kind == "frame":
                    image, event, dispatch, fps = rest
                    self._render_frame(image)
                    self._update_gesture_panels(event, dispatch, fps)
        except queue.Empty:
            pass
        self.after(30, self._poll_queue)

    def _on_camera_error(self, message: str) -> None:
        self.video_label.configure(
            text=f"Camera unavailable:\n{message}\n\nCheck connections/permissions and restart the app.",
            image="",
        )
        self.camera_status_label.configure(text="● Camera Error", text_color=RED)
        self.status_rows["Camera"].configure(text="Error", text_color=RED)

    def _render_frame(self, image: Image.Image) -> None:
        w = max(self.video_label.winfo_width(), 480)
        h = max(self.video_label.winfo_height(), 270)
        resized = image.resize((w, h))
        ctk_image = ctk.CTkImage(light_image=resized, dark_image=resized, size=(w, h))
        self.video_label.configure(image=ctk_image, text="")
        self.video_label.image = ctk_image  # keep a reference - CTkLabel won't hold one for us
        self.camera_status_label.configure(text="● Camera Connected", text_color=GREEN)
        self.status_rows["Camera"].configure(text="Connected", text_color=GREEN)

    def _update_gesture_panels(self, event, dispatch, fps: float) -> None:
        self.fps_label.configure(text=f"FPS {fps:.0f}")
        has_hand = event.name != "none"
        self.status_rows["Hand Tracking"].configure(
            text="Active" if has_hand else "Idle", text_color=GREEN if has_hand else TEXT_DIM
        )
        self.status_rows["Safety"].configure(
            text="Paused" if self.safety.paused else "Running", text_color=RED if self.safety.paused else GREEN
        )

        label = GESTURE_LABELS.get(event.name, event.name)
        self.gesture_card.configure(text=label)
        self.confidence_card.configure(text=f"{event.confidence:.0f}%")
        if has_hand:
            self._confidence_history.append(event.confidence)
            self._draw_sparkline()

        if event.is_trigger and event.name not in ("none", "unknown"):
            self._total_gestures += 1
        if dispatch is not None:
            self.action_card.configure(text=dispatch.label)
            self._recent_actions.appendleft((time.monotonic(), label, dispatch.label))
            self._refresh_recent_list()

    def _refresh_recent_list(self) -> None:
        for child in self.recent_list.winfo_children():
            child.destroy()
        if not self._recent_actions:
            self._recent_empty = ctk.CTkLabel(self.recent_list, text="No gestures yet.", text_color=TEXT_DIM)
            self._recent_empty.pack(padx=14, pady=14, anchor="w")
            return
        for ts, gesture_label, action_label in list(self._recent_actions)[:8]:
            row = ctk.CTkFrame(self.recent_list, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=6)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left")
            ctk.CTkLabel(left, text=gesture_label, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(left, text=action_label, font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w")
            age = max(0, int(time.monotonic() - ts))
            ctk.CTkLabel(row, text=f"{age}s ago", font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(side="right")

    def _draw_sparkline(self) -> None:
        canvas = self.sparkline
        canvas.delete("all")
        values = list(self._confidence_history)
        if len(values) < 2:
            return
        width = max(canvas.winfo_width(), 200)
        height = 50
        step = width / max(1, len(values) - 1)
        points = []
        for i, v in enumerate(values):
            x = i * step
            y = height - (v / 100.0) * (height - 6) - 3
            points.extend([x, y])
        canvas.create_line(*points, fill=ACCENT, width=2, smooth=True)

    def _tick_session_stats(self) -> None:
        elapsed = int(time.monotonic() - self._session_start)
        avg_conf = sum(self._confidence_history) / len(self._confidence_history) if self._confidence_history else 0.0
        mins, secs = divmod(elapsed, 60)
        self.stats_label.configure(
            text=f"Total Gestures   {self._total_gestures}\n"
                 f"Avg Confidence   {avg_conf:.0f}%\n"
                 f"Session Time     {mins:02d}:{secs:02d}"
        )
        self._refresh_recent_list()
        self.after(1000, self._tick_session_stats)

    # ------------------------------------------------------------------ end
    def _on_close(self) -> None:
        self._stop_event.set()
        self.destroy()

"""User Interface: shows video, landmarks, recognized gesture, settings, and
enable/disable status. The camera/tracking/gesture pipeline runs on a background
thread; Tkinter widgets are only ever touched from the main thread - the worker hands
over its newest frame through a one-slot buffer and everything else through a queue.
"""
from __future__ import annotations

import copy
import math
import platform
import subprocess
import webbrowser
import queue
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path

import customtkinter as ctk
import cv2
from PIL import Image

from .action_controller import ActionController, DispatchResult, NullExecutor, PyAutoGuiExecutor, continuous_poses
from .action_log import ActionLog, FrameLog, SessionStats, build_suggestions
from .camera import Camera, CameraError
from .clipboard import ClipboardHistory
from .config import DEFAULT_CONFIG, save_config
from .custom_gestures import CustomGestureError, CustomGestureStore
from .face_control import EXPRESSIONS, FACE_ACTIONS, POINTER_MODES, FaceDispatcher, FaceEngine, FaceTracker
from .gesture_engine import (
    BUILTIN_GESTURES, CUSTOM_PREFIX, OPTIONAL_GESTURES, GestureEngine, GestureEvent, pinch_distance,
)
from .hand_tracker import HandTracker, TrackerError
from .history import History
from .profiles import LATER_BUILTINS, PROFILE_KEYS, ProfileError, ProfileStore, builtin_profiles, clean_name
from .safety import SafetyGuard
from .smart_home import SmartHomeClient
from .system import RELEASES_PAGE, LoginItem, MenuBarIcon, check_for_update, launch_command, is_packaged
from .training import GestureDataset, ModelManager, TrainingError, train
from .assistant import (
    FREE_REQUEST_WORDS, SITES, AIError, LocalAI, Task, gmail_compose_url, interpret, mailto_url,
)
from .voice import Transcriber, VoiceListener, command_vocabulary, installed_apps, parse_command
from .ui_pages import PAGES, action_label
from .cctv_client import CctvService
from .ui_cctv import HomeCctvPage
from .ui_holo import HoloDeskPage
from .ui_keyboard import KeyboardWindow
from .virtual_keyboard import KeyboardModel, key_label
from .window_manager import WindowManager
from .ui_widgets import (
    ACCENT, ACCENT_HOVER, ACCENT_SOFT, AMBER, BG, BORDER, CATEGORIES, GREEN, JARVIS_ACCENT, ON_ACCENT, PANEL,
    PANEL_ALT, RED, TEXT, TEXT_DIM, TRACK, button, card, gesture_glyph, gesture_label, label, option_menu, resolve,
    set_accent, set_jarvis, switch,
)

__version__ = "1.3.0"

# (page name, subtitle, glyph)
NAV_ITEMS = [
    ("Dashboard", "Live overview", "⌂"),
    ("Holo Desk", "JARVIS workspace", "◈"),
    ("Learn", "Guided gesture lesson", "🎓"),
    ("Gesture Studio", "Mappings, sensitivity, practice", "✋"),
    ("Training Studio", "Teach your own gestures", "◉"),
    ("System Controls", "Mouse & Keyboard", "➤"),
    ("Media & Presentation", "Music, Video, Slides", "♫"),
    ("Smart Home", "IoT Device Control", "⌂"),
    ("Home CCTV", "Cameras & security", "📹"),
    ("AI Insights", "Usage & suggestions", "✦"),
    ("Voice", "Speak commands", "🎙"),
    ("Face Control", "Head & eyes, no hands", "🙂"),
    ("Clipboard", "Copy, paste, history", "❐"),
    ("Profiles", "Work, Presentation, Media…", "▤"),
    ("Settings", "General & Privacy", "⚙"),
]

# dashboard shortcut cards: (config key, description, page it opens)
FEATURE_CARDS = [
    ("mouse_control", "Move, click, scroll, drag", "System Controls"),
    ("media_control", "Play, pause, volume, skip", "Media & Presentation"),
    ("presentation", "Next, previous slide", "Media & Presentation"),
    ("smart_home", "Webhooks for your hub", "Smart Home"),
    ("ai_assistant", "Insights & custom gestures", "AI Insights"),
]

# A request that begins like this asks for something to be done. If it can't be worked
# out it is reported as not understood - it is never given to the AI as a question.
_ACTION_STARTS = ("open", "just open", "go to", "launch", "start", "play", "search", "find", "close", "click",
                  "scroll", "switch", "type", "send", "show", "visit", "minimize", "stop", "pause")
GUIDE_GESTURES = BUILTIN_GESTURES[:8]  # the dashboard guide has room for eight
_ENGINE_KEYS = ("pinch_threshold", "swipe_min_distance", "swipe_max_duration", "cooldown_seconds",
                "stable_frames", "pose_hold_seconds")
_CONTROLLER_KEYS = ("cursor_smoothing", "cursor_dead_zone", "active_area_margin", "scroll_speed",
                    "drag_hold_seconds", "engage_seconds", "double_click_seconds")
_RECORD_LIMIT = 300  # samples per recording run; it stops by itself there
PREVIEW_PAGES = ("Dashboard", "Training Studio", "Learn", "Face Control", "Holo Desk")
PAGES["Holo Desk"] = HoloDeskPage
PAGES["Home CCTV"] = HomeCctvPage
REST_EDGE = 0.98  # wrist this low in the frame = a resting hand (chosen from recorded real use)


def _bind_click(widget, callback) -> None:
    widget.bind("<Button-1>", lambda _e: callback())
    for child in widget.winfo_children():
        _bind_click(child, callback)


class App(ctk.CTk):
    def __init__(self, config: dict, config_path: Path, app_file: Path | None = None) -> None:
        super().__init__()
        self.config_data = config
        self.config_path = config_path
        root = config_path.parent.parent
        self.app_file = app_file or root / "app.py"
        self.login_item = LoginItem()
        self.menu_bar: MenuBarIcon | None = None
        self.update_info: dict | None = None  # the result of the last update check
        self.log_dir = root / "logs"
        self.model_path = root / "assets" / "models" / "hand_landmarker.task"

        set_jarvis(config["theme"] == "jarvis")
        ctk.set_appearance_mode("dark" if config["theme"] == "jarvis" else config["theme"])
        config["accent_color"] = set_accent(config["accent_color"])  # before any widget is built
        self.title("TouchlessX — Control Your World with Hand Gestures")
        width = min(1400, self.winfo_screenwidth() - 40)
        height = min(900, self.winfo_screenheight() - 80)
        self.geometry(f"{width}x{height}+20+25")
        self.minsize(1100, 700)
        self.configure(fg_color=BG)

        self.smart_home = SmartHomeClient(config_path.parent / "smart_home.json")
        self.dataset = GestureDataset(root / "data" / "gesture_samples.csv")
        self.models = ModelManager(root / "data" / "models")
        self.action_log = ActionLog(self.log_dir / "actions.log")
        self.frame_log = FrameLog(self.log_dir / "frames.csv")
        self.history = History(root / "data" / "history.db")
        self.history.prune(int(config["history_retention_days"]))
        self.profiles = ProfileStore(config_path.parent / "profiles.json")
        if config["active_profile"] not in self.profiles.profiles:
            # The active profile's file entry is gone: recreate it from what is in use now.
            try:
                config["active_profile"] = clean_name(config["active_profile"])
            except ProfileError:
                config["active_profile"] = "Default"
            self.profiles.set(config["active_profile"], config)
        for name in LATER_BUILTINS:  # new built-in profiles reach existing users once
            if name not in config["profiles_added"]:
                config["profiles_added"].append(name)
                if name not in self.profiles.profiles:
                    self.profiles.set(name, builtin_profiles()[name])
        self.last_event = None
        self.mini: MiniController | None = None
        self._mini_gesture = "–"
        self.stats = SessionStats()
        self.safety = SafetyGuard()
        self.gesture_engine = GestureEngine(custom_classifier=self.models.load_active())

        self._input_unavailable = False
        try:
            self.executor = PyAutoGuiExecutor()
        except Exception:
            self.executor = NullExecutor()
            self._input_unavailable = True
        self.action_controller = ActionController(self.executor, self.safety, {}, smart_home=self.smart_home)
        # Home CCTV: HomeSmartCCTV cameras. Network work runs on the service's own thread;
        # the controller only hands it command names (after any confirmation gesture).
        self.cctv = CctvService(config["cctv_server_url"])
        self.cctv.select_device(config["cctv_device_id"])
        if self.cctv.signed_in:
            self.cctv.refresh_devices()
        self.action_controller.callbacks["cctv"] = self.cctv.send
        # Native window management (move / resize / snap the front window).
        self.action_controller.window_manager = WindowManager()
        # The virtual keyboard: a pinch over it types a key instead of clicking.
        self.keyboard_model = KeyboardModel()
        self.keyboard_window: KeyboardWindow | None = None
        self.action_controller.callbacks["pointer_click"] = self._keyboard_click
        self.action_controller.callbacks["pointer_blocked"] = self._over_keyboard
        self.action_controller.callbacks["toggle_keyboard"] = lambda: self._events.put(("toggle_keyboard",))
        # Face Control: hands-free mode, used instead of the hand pipeline while it is on.
        self.face_model_path = root / "assets" / "models" / "face_landmarker.task"
        self.face_engine = FaceEngine()
        self.face_dispatcher = FaceDispatcher(self.action_controller, self.safety)
        self._face_mode = False
        self._face_tracker: FaceTracker | None = None  # created on the worker thread when first needed
        self.last_face_event = None
        self.face_error = ""
        # Called on the worker thread, so it only posts a message for the UI thread.
        self.action_controller.callbacks["show_app"] = lambda: self._events.put(("show_window",))
        self.executor.own_window_handler = lambda what: self._events.put(("own_window", what))
        self.clipboard = ClipboardHistory()
        self.voice: VoiceListener | None = None
        self.local_ai = LocalAI()
        self._voice_waiting_until = 0.0       # after the wake word alone: waiting for the request
        self.voice_apps: dict[str, str] = {}
        self.voice_status = ("off", "")       # (state, detail) from the listener
        self.voice_heard = ""                 # the last phrase recognised, for the Voice page
        self.voice_result = ""                # what was done about it
        self.action_controller.callbacks["paste_previous"] = self._request_paste_previous
        self._practice = False
        self._preferred_hand = "any"
        self._mirror_preview = True

        self.camera = Camera(config["camera_index"], config["frame_width"], config["frame_height"])
        self._pending_camera: Camera | None = None

        self._events: queue.Queue = queue.Queue()
        self._latest: tuple | None = None
        self._latest_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._tracking_wanted = threading.Event()
        self._tracking_wanted.set()
        self._camera_running = False
        self._auto_retries = 0
        self._show_landmarks = True
        self._recording: dict | None = None
        self._record_result = ""
        self._recent: deque = deque(maxlen=5)
        self._save_job = None
        self._loading = False
        self._shown: dict[str, object] = {}  # last value pushed to each live widget
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._current_page = "Dashboard"

        self.toggle_vars: dict[str, ctk.BooleanVar] = {}
        for key, *_ in CATEGORIES:
            var = ctk.BooleanVar(value=bool(config["enabled"].get(key, False)))
            var.trace_add("write", lambda *_a, k=key: self._on_toggle(k))
            self.toggle_vars[key] = var

        self._blank_image = ctk.CTkImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), size=(1, 1))
        self._build_layout()
        self._apply_config()

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<space>", self._on_pause_key)
        self.bind("<Escape>", lambda _e: self._set_paused(True))
        self.after(15, self._poll)
        self.after(1000, self._tick)
        if config["mini_controller"]:
            self.after(600, lambda: self.set_mini_controller(True, save=False))
        if config["voice_enabled"]:
            self.after(900, lambda: self.set_voice(True, save=False))
        if config["menu_bar_icon"]:
            self.after(500, lambda: self.set_menu_bar(True, save=False))
        if config["check_updates"]:
            self.after(4000, self.check_updates)
        if not config["onboarded"]:
            self.after(300, lambda: self.show_page("Getting Started"))

    # ------------------------------------------------------- state / settings
    def _apply_config(self) -> None:
        """Pushes every value in `config_data` into the live pipeline objects."""
        config, sens = self.config_data, self.config_data["sensitivity"]
        for key in _ENGINE_KEYS:
            setattr(self.gesture_engine, key, sens[key])
        for key in _CONTROLLER_KEYS:
            setattr(self.action_controller, key, sens[key])
        self.safety.confidence_threshold = sens["confidence_threshold"]
        self.action_controller.enabled = config["enabled"]
        self.action_controller.mappings = config["mappings"]
        self._sync_drag()
        self.action_controller.presentation_keys = config["presentation_keys"]
        self.action_controller.shortcuts = config["shortcuts"]
        self.action_controller.confirm_smart_home = bool(config["confirm_smart_home"])
        self.action_controller.scroll_invert = bool(config["scroll_invert"])
        self.action_controller.scroll_style = config["scroll_style"]
        self.action_controller.scroll_vertical_only = bool(config["scroll_vertical_only"])
        self.action_controller.apps = config["apps"]
        self.action_controller.emergency_gesture = config["emergency_gesture"]
        calibration = config["calibration"]
        self.action_controller.calibration = tuple(calibration) if calibration else None
        self._preferred_hand = config["preferred_hand"]
        self._mirror_preview = bool(config["mirror_preview"])
        self.gesture_engine.rest_edge = REST_EDGE if config["ignore_resting_hand"] else None
        self._sync_gestures()
        self.history.enabled = bool(config["history_enabled"])
        self.action_log.enabled = bool(config["action_log_enabled"])
        self.frame_log.enabled = bool(config["action_log_enabled"])
        self._show_landmarks = bool(config["show_landmarks"])
        self._apply_face()
        self._loading = True  # syncing the switches to the config is not a change to save
        for key, var in self.toggle_vars.items():
            var.set(bool(config["enabled"].get(key, False)))
        self._loading = False
        self._refresh_guide()

    def _schedule_save(self) -> None:
        if self._loading:
            return
        # Debounced: dragging a slider fires dozens of changes a second.
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        # Whatever is live belongs to the active profile, so editing a setting edits the profile.
        self.profiles.set(self.config_data["active_profile"], self.config_data)
        save_config(self.config_path, self.config_data)

    def _on_toggle(self, key: str) -> None:
        value = bool(self.toggle_vars[key].get())
        self.config_data["enabled"][key] = value
        if key == "ai_assistant":
            self.gesture_engine.custom_enabled = value
        status = self._quick_status.get(key)
        if status is not None:
            status.configure(text="Enabled" if value else "Disabled", text_color=GREEN if value else TEXT_DIM)
        self._schedule_save()

    def set_sensitivity(self, key: str, value: float) -> None:
        self.config_data["sensitivity"][key] = value
        if key in _ENGINE_KEYS:
            setattr(self.gesture_engine, key, value)
        elif key in _CONTROLLER_KEYS:
            setattr(self.action_controller, key, value)
        elif key == "confidence_threshold":
            self.safety.confidence_threshold = value
        self._schedule_save()

    def set_option(self, key: str, value) -> None:
        self.config_data[key] = value
        if key == "drag_enabled":
            self._sync_drag()
        elif key == "presentation_keys":
            self.action_controller.presentation_keys = value
        elif key == "action_log_enabled":
            self.action_log.enabled = bool(value)
            self.frame_log.enabled = bool(value)
        elif key == "show_landmarks":
            self._show_landmarks = bool(value)
        elif key == "scroll_invert":
            self.action_controller.scroll_invert = bool(value)
        elif key == "scroll_style":
            self.action_controller.scroll_style = value
        elif key == "scroll_vertical_only":
            self.action_controller.scroll_vertical_only = bool(value)
        elif key == "ignore_resting_hand":
            self.gesture_engine.rest_edge = REST_EDGE if value else None
        elif key in ("voice_wake_word", "assistant_enabled"):
            self.restart_voice()
        elif key == "clipboard_history" and not value:
            self.clipboard.clear()
        elif key == "emergency_gesture":
            self.action_controller.emergency_gesture = value
        elif key == "calibration":
            self.action_controller.calibration = tuple(value) if value else None
        elif key == "preferred_hand":
            self._preferred_hand = value
        elif key == "mirror_preview":
            self._mirror_preview = bool(value)
        elif key == "confirm_smart_home":
            self.action_controller.confirm_smart_home = bool(value)
        elif key == "history_enabled":
            self.history.enabled = bool(value)
        elif key == "history_retention_days":
            self.history.prune(int(value))
        elif key == "accent_color":
            self.config_data[key] = set_accent(value)
            # Every widget holds the same colour lists, so redrawing them is enough.
            ctk.AppearanceModeTracker.update_callbacks()
            self._shown.clear()
            self._sync_pause_ui()
            self._highlight_nav(self._current_page)
            self._draw_chart()
        elif key == "theme":
            set_jarvis(value == "jarvis")
            if value == "jarvis":
                self.config_data["accent_color"] = set_accent(JARVIS_ACCENT)
            ctk.set_appearance_mode("dark" if value == "jarvis" else value)
            ctk.AppearanceModeTracker.update_callbacks()  # the palette lists changed in place
            self.configure(fg_color=BG)
            self._shown.clear()  # cached colours are stale now
            self._sync_pause_ui()
            self._highlight_nav(self._current_page)
            self._draw_chart()
        self._schedule_save()

    def set_shortcut(self, name: str, keys: list[str] | None) -> None:
        """Adds/replaces a named keyboard shortcut, or removes it when `keys` is None."""
        if keys is None:
            self.config_data["shortcuts"].pop(name, None)
            self.unmap_action("shortcut:" + name)
        else:
            self.config_data["shortcuts"][name] = keys
        self._schedule_save()

    # ----------------------------------------------------------- face control
    def _apply_face(self) -> None:
        face = self.config_data["face"]
        self.face_engine.wink_threshold = face["wink_threshold"]
        self.face_engine.long_blink_seconds = face["long_blink_seconds"]
        self.face_engine.scroll_speed = face["scroll_speed"]
        expressions = face["expressions"]
        self.face_engine.actions = {name: expressions.get(name, {}).get("action", "none") for name in EXPRESSIONS}
        for name, action in self.face_engine.actions.items():
            if action not in FACE_ACTIONS:
                self.face_engine.actions[name] = "none"  # the config file is editable
        self.face_engine.thresholds = {name: float(expressions.get(name, {}).get("threshold", 0.6))
                                       for name in EXPRESSIONS}
        self.face_dispatcher.smoothing = face["smoothing"]
        self.face_dispatcher.gain = face["gain"]
        self.face_dispatcher.pointer_mode = face["pointer_mode"] if face["pointer_mode"] in POINTER_MODES \
            else "relative"
        on = bool(self.config_data["face_control"])
        if on != self._face_mode:
            self.action_controller.release()
            self.gesture_engine.reset()
            self.face_engine.reset()
            self._face_mode = on
        if hasattr(self, "face_badge"):
            if on:
                self.face_badge.pack(side="right", padx=8, before=self.camera_button)
            else:
                self.face_badge.pack_forget()

    def set_face_control(self, on: bool) -> None:
        self.config_data["face_control"] = bool(on)
        self._apply_face()
        self._schedule_save()

    def set_face_setting(self, key: str, value: float) -> None:
        self.config_data["face"][key] = value
        self._apply_face()
        self._schedule_save()

    def set_face_expression(self, name: str, key: str, value) -> None:
        """`key` is "action" or "threshold"."""
        self.config_data["face"]["expressions"].setdefault(name, {})[key] = value
        self._apply_face()
        self._schedule_save()

    def face_recenter(self) -> None:
        self.face_engine.recenter()

    def _face_step(self, frame, now: float):
        """One frame of Face Control, on the worker thread: (face, event, dispatch)."""
        if self._face_tracker is None:
            try:
                self._face_tracker = FaceTracker(self.face_model_path)
            except TrackerError as exc:
                self._face_mode = False  # back to hands from the next frame; the UI thread tidies up
                self._events.put(("face_error", str(exc)))
                return None, GestureEvent("none", 0.0, None, None, False), None
        face = self._face_tracker.process(frame)
        face_event = self.face_engine.update(face, now)
        self.last_face_event = face_event
        self.face_dispatcher.neutral = self.face_engine.neutral
        try:
            dispatch = self.face_dispatcher.dispatch(face_event, now)
        except Exception:
            dispatch = None  # an OS input hiccup must not take tracking down with it
        event = GestureEvent(face_event.name, 100.0 if face is not None else 0.0, None, None, face_event.trigger)
        return face, event, dispatch

    # -------------------------------------------------------- virtual keyboard
    def toggle_keyboard(self) -> None:
        if self.keyboard_window is not None and self.keyboard_window.winfo_exists():
            self.keyboard_model.visible = False
            self.keyboard_model.set_layout(None, [])
            self.keyboard_window.destroy()
            self.keyboard_window = None
            return
        self.keyboard_window = KeyboardWindow(self)
        self.keyboard_model.visible = True

    def _pointer_position(self) -> tuple[float, float] | None:
        position = getattr(self.executor, "position", None)
        try:
            return position() if position is not None else None
        except Exception:
            return None

    def _over_keyboard(self) -> bool:
        if not self.keyboard_model.visible:
            return False
        position = self._pointer_position()
        return position is not None and self.keyboard_model.contains(*position)

    def _keyboard_click(self, action: str) -> DispatchResult | None:
        """Called on the tracking thread for every click. Over the virtual keyboard the
        click becomes a key typed into the app in front; elsewhere it is left alone."""
        if not self._over_keyboard():
            return None
        position = self._pointer_position()
        key = self.keyboard_model.key_at(*position) if position is not None else None
        result = self.keyboard_model.press(key, time.monotonic()) if key is not None else None
        if result is None:
            return DispatchResult("Keyboard", "")  # between keys, or too soon: swallowed, not clicked
        self._send_key(result)
        self._events.put(("key_flash", key))
        return DispatchResult(f"Key {key_label(key)}", "")

    def keyboard_type(self, key: str) -> None:
        """A key pressed with a real mouse (on the UI thread)."""
        result = self.keyboard_model.press(key, time.monotonic())
        if result is not None:
            self._send_key(result)
            if self.keyboard_window is not None:
                self.keyboard_window.flash(key)

    def _send_key(self, result: tuple[str, str]) -> None:
        executor = self.action_controller.executor  # the null one in practice mode
        kind, value = result
        if kind == "text":
            executor.type_text(value)
        elif kind == "key":
            executor.press_key(value)

    def _sync_drag(self) -> None:
        # The Holo Desk's panels are moved with a held pinch, so drag is on while it is open.
        self.action_controller.drag_enabled = bool(self.config_data["drag_enabled"]) or \
            self._current_page == "Holo Desk"

    def set_practice(self, on: bool) -> None:
        """Practice mode: gestures are recognised and shown, but nothing is sent."""
        self._practice = bool(on)
        self._update_dry_run()

    def _update_dry_run(self) -> None:
        # Training is kept apart from live control: while the Training Studio is open,
        # the poses being recorded must not click or scroll anything.
        # The guided lesson likewise: a learner's first pinches must not click anything.
        training = self._current_page == "Training Studio"
        learning = self._current_page == "Learn"
        on = self._practice or training or learning
        if on != self.action_controller.dry_run:
            self.action_controller.release()
            self.action_controller.dry_run = on
        if on:
            self.practice_badge.configure(text="TRAINING · CONTROL OFF" if training else
                                          "LESSON · PRACTICE MODE" if learning else "PRACTICE MODE")
            self.practice_badge.pack(side="right", padx=8, before=self.camera_button)
        else:
            self.practice_badge.pack_forget()
        self._refresh_mini()

    def set_app(self, name: str, path: str | None) -> None:
        """Adds a launchable application, or removes it when `path` is None."""
        if path is None:
            self.config_data["apps"].pop(name, None)
            self.unmap_action("app:" + name)
        else:
            self.config_data["apps"][name] = path
        self._schedule_save()

    def set_gesture_setting(self, gesture: str, key: str, value) -> None:
        """Per-gesture override: "enabled" (bool), "confidence" or "cooldown" (number,
        or None to follow the global setting)."""
        settings = self.config_data["gesture_settings"].setdefault(gesture, {})
        if value is None or (key == "enabled" and value):
            settings.pop(key, None)
        else:
            settings[key] = value
        if not settings:
            self.config_data["gesture_settings"].pop(gesture, None)
        self._sync_gestures()
        self._schedule_save()

    def set_mini_controller(self, on: bool, save: bool = True) -> None:
        if on and self.mini is None:
            self.mini = MiniController(self)
            self._shown.pop("mini", None)
            self._refresh_mini()
        elif not on and self.mini is not None:
            self.mini.destroy()
            self.mini = None
        if save:
            self.config_data["mini_controller"] = bool(on)
            self._schedule_save()

    # ------------------------------------------------------------------ system
    def set_menu_bar(self, on: bool, save: bool = True) -> None:
        """Shows or removes the menu-bar icon (macOS only; elsewhere this does nothing)."""
        if on and self.menu_bar is None and platform.system() == "Darwin":
            try:
                self.menu_bar = MenuBarIcon({
                    "show": self.show_window, "pause": self.toggle_pause, "camera": self._toggle_tracking,
                    "voice": lambda: self.set_voice(self.voice is None), "quit": self.quit_app,
                })
            except Exception:
                self.menu_bar = None  # the icon is a convenience; the app works without it
        elif not on and self.menu_bar is not None:
            self.menu_bar.remove()
            self.menu_bar = None
        if save:
            self.config_data["menu_bar_icon"] = bool(on)
            self._schedule_save()
        self._sync_menu_bar()

    def _sync_menu_bar(self) -> None:
        if self.menu_bar is not None:
            self.menu_bar.update(self.safety.paused, self._tracking_wanted.is_set(), self.voice is not None)

    def set_start_at_login(self, on: bool) -> str:
        """Adds or removes the per-user login entry; returns a message for the settings page."""
        try:
            if on:
                self.login_item.enable(launch_command(self.app_file))
                return "TouchlessX will start when you log in."
            self.login_item.disable()
            return "TouchlessX will no longer start at login."
        except OSError as exc:
            return f"Could not change the login setting: {exc}"

    def check_updates(self, done=None) -> None:
        """Looks for a newer release on a helper thread; `done(result)` runs on the UI thread."""
        def work() -> None:
            self._events.put(("update_checked", check_for_update(__version__), done))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ voice
    def set_voice(self, on: bool, save: bool = True) -> None:
        """Starts or stops listening for spoken commands. The microphone is only ever
        open while this is on, and the header shows it."""
        if on and self.voice is None:
            self.voice_apps = installed_apps(self.config_data["apps"])
            folder = self.model_path.parent
            assistant = bool(self.config_data["assistant_enabled"])
            transcriber = Transcriber(folder / "whisper") if assistant else None
            if transcriber is not None:
                names = ", ".join(sorted({n.title() for n in SITES if " " not in n})[:40])
                transcriber.prompt = f"Computer, open YouTube and search for a song. Write a mail. Open {names}."
            self.voice = VoiceListener(
                folder / "voice", command_vocabulary(self.voice_apps) + (FREE_REQUEST_WORDS if assistant else []),
                on_heard=lambda kind, text: self._events.put(("voice_heard", kind, text)),
                on_state=lambda state, detail: self._events.put(("voice_state", state, detail)),
                wake=self.config_data["voice_wake_word"], app_names=self.voice_apps,
                transcriber=transcriber,
                free_words=FREE_REQUEST_WORDS if assistant else (),
            )
            self.voice.start()
        elif not on and self.voice is not None:
            self.voice.stop()
            self.voice = None
            self.voice_status = ("off", "")
            self.action_controller.stop_scrolling()
        if save:
            self.config_data["voice_enabled"] = bool(on)
            self._schedule_save()
        self._sync_voice_ui()
        self._sync_menu_bar()

    def restart_voice(self) -> None:
        """Applies a changed wake word or assistant switch to a running listener."""
        if self.voice is not None:
            self.set_voice(False, save=False)
            self.after(700, lambda: self.set_voice(True, save=False))

    def _sync_voice_ui(self) -> None:
        state = self.voice_status[0]
        if self.voice is None:
            self.voice_badge.pack_forget()
            return
        text, color = {
            "loading": ("🎙  Loading…", TEXT_DIM), "listening": ("🎙  Listening", GREEN),
            "thinking": ("🎙  Working…", AMBER), "silent": ("🎙  No sound", AMBER),
            "error": ("🎙  Voice error", RED),
        }.get(state, ("🎙  Starting…", TEXT_DIM))
        if state == "listening" and time.monotonic() < self._voice_waiting_until:
            text, color = "🎙  Yes?", AMBER
        self.voice_badge.configure(text=text, text_color=color)
        self.voice_badge.pack(side="right", padx=8, before=self.camera_button)

    def _on_voice_heard(self, kind: str, text: str) -> None:
        """One phrase from the listener: a plain command, a free request, the wake word
        alone, or speech that was not for us."""
        self.voice_heard = text
        self._voice_waiting_until = 0.0
        wake = self.config_data["voice_wake_word"]
        if kind == "ignored":
            self.voice_result = f"Ignored - say \"{wake}\" first."
        elif kind == "wake":
            self._voice_waiting_until = time.monotonic() + 6.0
            self.voice_result = "Yes? Listening for your request…"
        else:
            request = parse_command(text, self.voice_apps) if kind == "command" else interpret(text, self.voice_apps)
            wants_action = text.lower().lstrip().startswith(_ACTION_STARTS)
            if request is None and kind == "free" and self.config_data["assistant_enabled"] and not wants_action:
                request = Task("answer", f"Answer: {text[:48]}", text=text)  # a question or remark: ask the AI
            if request is None:
                self.voice_result = f"I didn't understand: \"{text}\""
            elif isinstance(request, Task):
                self.voice_result = self._run_task(request)
            else:
                self.voice_result = self._run_voice_command(request)
        self._sync_voice_ui()

    def _note_voice_action(self, label: str) -> None:
        practice = self.action_controller.dry_run
        shown = f"Practice: {label}" if practice else label
        self._recent.appendleft((time.monotonic(), "voice", shown))
        self.action_card.configure(text=shown)
        self._refresh_recent()
        if not practice:
            self.action_log.record("voice", label, 100)
            self.history.record("voice", label, 100)

    def _run_voice_command(self, command) -> str:
        controller = self.action_controller
        if command.kind == "resume":
            self._set_paused(False)
            return "Control resumed."
        if self.safety.paused:
            return "Stopped - say \"computer, resume\" or press Resume first."
        try:
            if command.kind == "scroll":
                done = controller.voice_scroll(command.direction, command.speed)
            elif command.kind == "stop_scroll":
                done = controller.stop_scrolling() or True
            elif command.kind == "open_app":
                if not controller.dry_run:
                    self.executor.open_app(self.voice_apps[command.app])
                done = True
            else:
                done = controller.run_action(command.action)
        except Exception as exc:
            return f"Could not do that: {exc}"
        if not done:
            return f"\"{command.label}\" is switched off or not available right now."
        self._sync_pause_ui()  # the command may have stopped or locked control
        self._note_voice_action(command.label)
        return f"Done: {command.label}"

    # -------------------------------------------------------------- assistant
    def _run_task(self, task: Task) -> str:
        """Carries out an assistant task. Tasks that need the language model run on a
        helper thread; the result comes back as an "assistant_done" event."""
        if self.safety.paused:
            return "Stopped - say \"computer, resume\" or press Resume first."
        if task.kind == "open_url":
            if not self.action_controller.dry_run:
                webbrowser.open(task.url)
            self._note_voice_action(task.label)
            return f"Done: {task.label}"
        if task.kind == "type_text":
            if not self.action_controller.dry_run:
                self._paste_text(task.text)
            self._note_voice_action(task.label)
            return f"Typed: {task.text}"
        if not self.config_data["assistant_enabled"]:
            return "That needs the AI assistant, which is switched off on the Voice page."
        threading.Thread(target=self._think, args=(task,), daemon=True).start()
        self.voice_status = ("thinking", "")
        return f"Working on it: {task.label}…"

    def _think(self, task: Task) -> None:
        """Helper thread: the language model produces text - and only text."""
        try:
            if task.kind == "write_email":
                result = self.local_ai.write_email(task.topic, task.to, self.config_data["user_name"])
            elif task.kind == "write_text":
                result = self.local_ai.write_text(task.text)
            else:
                result = self.local_ai.answer(task.text)
        except AIError as exc:
            result = exc
        self._events.put(("assistant_done", task, result))

    def _finish_task(self, task: Task, result) -> None:
        if self.voice is not None and self.voice_status[0] == "thinking":
            self.voice_status = ("listening", "")
        if isinstance(result, Exception):
            self.voice_result = str(result)
        elif self.safety.paused:
            self.voice_result = "Stopped while I was working - nothing was done."
        elif task.kind == "write_email":
            subject, body = result
            if not self.action_controller.dry_run:
                build = mailto_url if self.config_data["email_target"] == "mail" else gmail_compose_url
                webbrowser.open(build(subject, body, task.to))
            self._note_voice_action(task.label)
            self.voice_result = f"Draft opened - check it before you send.\n\nSubject: {subject}\n\n{body}"
        elif task.kind == "write_text":
            if not self.action_controller.dry_run:
                self._paste_text(result)
            self._note_voice_action(task.label)
            self.voice_result = f"Written where your cursor is (also on the clipboard):\n\n{result}"
        else:
            self._note_voice_action(task.label)
            self.voice_result = result
            if self.config_data["speak_replies"] and platform.system() == "Darwin" and not self.action_controller.dry_run:
                try:
                    subprocess.Popen(["say", result[:600]])  # an argument list, never a shell string
                except OSError:
                    pass
        self._sync_voice_ui()

    def _paste_text(self, text: str) -> None:
        self.copy_to_clipboard(text)
        # Give the system a moment to take the new clipboard before the paste keystroke.
        self.after(150, lambda: self.action_controller.run_action("paste", force=True))

    # -------------------------------------------------------------- clipboard
    def _request_paste_previous(self) -> bool:
        """Worker thread: is there an earlier copy? If so the UI thread pastes it."""
        if self.clipboard.previous() is None:
            return False
        self._events.put(("paste_clip", 1))
        return True

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()  # hand it to the system clipboard now
        self.clipboard.add(text)

    def _paste_clip(self, index: int) -> None:
        if index >= len(self.clipboard.items):
            return
        self.copy_to_clipboard(self.clipboard.items[index])
        # Give the system a moment to take the new clipboard before the paste keystroke.
        self.after(120, lambda: self.action_controller.run_action("paste", force=True))

    def _watch_clipboard(self) -> None:
        if not self.config_data["clipboard_history"]:
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return  # empty, or not text (an image, a file)
        if self.clipboard.add(text):
            page = self._pages.get("Clipboard")
            if page is not None and self._current_page == "Clipboard":
                page.rebuild()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def finish_onboarding(self) -> None:
        self.config_data["onboarded"] = True
        self._schedule_save()
        self.show_page("Dashboard")

    # --------------------------------------------------------------- profiles
    def switch_profile(self, name: str) -> None:
        if name not in self.profiles.profiles or name == self.config_data["active_profile"]:
            self.profile_menu.set(self.config_data["active_profile"])
            return
        if self._save_job is not None:  # pending edits belong to the profile being left
            self.after_cancel(self._save_job)
            self._save_now()
        self.action_controller.release()
        self.config_data.update(self.profiles.get(name))
        self.config_data["active_profile"] = name
        self._apply_config()
        self._schedule_save()
        self.refresh_profiles()
        self._rebuild_pages()

    def refresh_profiles(self) -> None:
        """Re-syncs the header's profile picker after profiles were added/renamed/removed."""
        self.profile_menu.configure(values=self.profiles.names())
        self.profile_menu.set(self.config_data["active_profile"])

    def _rebuild_pages(self) -> None:
        for page in self._pages.values():
            page.destroy()
        self._pages.clear()
        if self._current_page != "Dashboard":
            self.show_page(self._current_page)

    def _sync_gestures(self) -> None:
        """Derives everything the engine and controller need to know per gesture from
        the mappings and the gesture library settings."""
        controller, engine = self.action_controller, self.gesture_engine
        settings = self.config_data["gesture_settings"]
        controller.disabled_gestures = {g for g, s in settings.items() if s.get("enabled") is False}
        controller.gesture_confidence = {g: s["confidence"] for g, s in settings.items() if "confidence" in s}
        engine.cooldowns = {g: s["cooldown"] for g, s in settings.items() if "cooldown" in s}
        used = {controller.emergency_gesture}
        engine.muted = {g for g in OPTIONAL_GESTURES if controller.action_for(g) == "none" and g not in used}
        # One shared set, so the engine and the controller always agree on which poses are held.
        kinds = continuous_poses({g: controller.action_for(g) for g in controller.mappings})
        engine.continuous_gestures = controller.continuous_gestures = kinds

    def set_mapping(self, gesture: str, action: str) -> None:
        self.config_data["mappings"][gesture] = action
        self._sync_gestures()
        self._refresh_guide()
        self._schedule_save()

    def unmap_gesture(self, gesture: str) -> None:
        if self.config_data["mappings"].pop(gesture, None) is not None:
            self._schedule_save()

    def unmap_action(self, action: str) -> None:
        for gesture, mapped in list(self.config_data["mappings"].items()):
            if mapped == action:
                self.set_mapping(gesture, "none")

    def reset_settings(self) -> None:
        """Puts the active profile's mappings, sensitivity and switches back to defaults.
        Camera, theme, privacy choices, shortcuts and other profiles are left alone."""
        self.action_controller.release()
        for key in PROFILE_KEYS:
            self.config_data[key] = copy.deepcopy(DEFAULT_CONFIG[key])
        self._apply_config()
        self._schedule_save()
        self._rebuild_pages()

    def all_gestures(self) -> list[str]:
        return list(BUILTIN_GESTURES) + [CUSTOM_PREFIX + name for name in self.dataset.labels()]

    @property
    def camera_running(self) -> bool:
        return self._camera_running

    def input_ok(self) -> bool:
        return not self._input_unavailable and self.executor.has_permission()

    def suggestions(self) -> list[str]:
        return build_suggestions(
            self.stats, camera_ok=self._camera_running, input_ok=self.input_ok(), paused=self.safety.paused,
            custom_gestures=len(self.dataset.labels()), gesture_label=gesture_label,
            brief_poses=self.gesture_engine.brief_poses,
        )

    def versions_text(self) -> str:
        import mediapipe

        return (
            f"TouchlessX {__version__}  ·  Python {platform.python_version()}  ·  {platform.system()} {platform.release()}\n"
            f"OpenCV {cv2.__version__}  ·  MediaPipe {mediapipe.__version__}  ·  "
            f"CustomTkinter {ctk.__version__}  ·  Tk {tk.TkVersion}"
        )

    def test_action(self, action: str, delay_ms: int, done) -> None:
        """Fires one action from a UI test button (bypasses pause and category toggles)."""
        def fire() -> None:
            ok = self.input_ok() and not self.action_controller.dry_run
            if ok:
                try:
                    ok = self.action_controller.run_action(action, force=True) is not None
                except Exception:
                    ok = False
            done(ok)

        self.after(delay_ms, fire)

    # --------------------------------------------------------- camera control
    def start_tracking(self) -> None:
        self._tracking_wanted.set()
        self._sync_menu_bar()
        self._set_camera_status("● Starting camera…", TEXT_DIM)

    def stop_tracking(self) -> None:
        self._auto_retries = 99  # an explicit stop must not be undone by the auto-retry
        self._tracking_wanted.clear()
        self._sync_menu_bar()

    def _toggle_tracking(self) -> None:
        if self._tracking_wanted.is_set():
            self.stop_tracking()
        else:
            self._auto_retries = 0
            self.start_tracking()

    def restart_camera(self, index: int, width: int, height: int) -> None:
        self.config_data.update(camera_index=index, frame_width=width, frame_height=height)
        self._schedule_save()
        self._auto_retries = 0
        self._pending_camera = Camera(index, width, height)
        self.start_tracking()

    # ---------------------------------------------------------------- training
    def start_recording(self, name: str) -> None:
        name = CustomGestureStore.validate_name(name)
        if not self._camera_running:
            raise CustomGestureError("Start the camera first.")
        self._recording = {"name": name, "samples": [], "start": time.monotonic() + 2.0, "last": 0.0,
                           "paused": False, "done": False}

    def pause_recording(self, paused: bool) -> None:
        if self._recording is not None:
            self._recording["paused"] = paused

    def stop_recording(self) -> None:
        """Ends the recording run and adds what was captured to the dataset."""
        rec, self._recording = self._recording, None
        if rec is None:
            return
        rec["done"] = True
        if not rec["samples"]:
            self._record_result = "Nothing was recorded - no hand was visible."
            return
        try:
            total = self.dataset.add_samples(rec["name"], rec["samples"])
        except CustomGestureError as exc:
            self._record_result = str(exc)
            return
        self.config_data["mappings"].setdefault(CUSTOM_PREFIX + rec["name"], "none")
        self._schedule_save()
        self._record_result = (f"Added {len(rec['samples'])} samples to '{rec['name']}' ({total} in total). "
                               "Train the model to use them.")

    def recording_status(self) -> tuple[str, bool]:
        """(status text, is a recording in progress?)."""
        rec = self._recording
        if rec is None:
            return self._record_result, False
        remaining = rec["start"] - time.monotonic()
        if remaining > 0:
            return f"Get ready to hold '{rec['name']}'…  {math.ceil(remaining)}", True
        state = "Paused" if rec["paused"] else "Recording"
        return f"{state} '{rec['name']}' - {len(rec['samples'])} samples this run", True

    def train_model(self) -> dict:
        """Trains on the dataset, stores the model as a new version and starts using it.
        Returns the evaluation report; raises TrainingError if there is too little data."""
        bundle = train(self.dataset)
        self.models.save(bundle)
        self.gesture_engine.custom_classifier = self.models.load_active()
        self.toggle_vars["ai_assistant"].set(True)  # a model nobody consults would be pointless
        return bundle["report"]

    def use_model(self, version: int) -> None:
        self.gesture_engine.custom_classifier = self.models.activate(version)

    def remove_training_label(self, name: str) -> None:
        self.dataset.delete(name)
        self.unmap_gesture(CUSTOM_PREFIX + name)

    def rename_training_label(self, old: str, new: str) -> str:
        new = self.dataset.rename(old, new)
        mappings = self.config_data["mappings"]
        if new != old and CUSTOM_PREFIX + old in mappings:
            mappings[CUSTOM_PREFIX + new] = mappings.pop(CUSTOM_PREFIX + old)
            self._sync_gestures()
            self._schedule_save()
        return new

    # ----------------------------------------------------------------- pause
    def _on_pause_key(self, _event) -> None:
        if isinstance(self.focus_get(), tk.Entry):
            return  # typing a space into a text field
        self._set_paused(not self.safety.paused)

    def toggle_pause(self) -> None:
        self._set_paused(not self.safety.paused)

    def _set_paused(self, paused: bool) -> None:
        self.safety.set_paused(paused)
        if paused:
            self.action_controller.release()
        self._sync_pause_ui()

    def emergency_stop(self) -> None:
        """The big red button: stops all gesture control at once. Works whatever the
        camera, tracker or gesture state is - it never depends on a gesture being seen."""
        if self.safety.paused:
            self._set_paused(False)
        else:
            self._set_paused(True)
            self.safety.set_locked(False)  # resuming should not land in a second, hidden block
            self._sync_pause_ui()

    def unlock(self) -> None:
        self.safety.set_locked(False)
        self._sync_pause_ui()

    def _sync_pause_ui(self) -> None:
        state = (self.safety.paused, self.safety.locked)
        if self._shown.get("safety") == state:
            return
        self._shown["safety"] = state
        paused, locked = state
        self.pause_button.configure(
            text="▶  Resume" if paused else "❚❚  Pause", fg_color=RED if paused else ACCENT,
            hover_color=RED if paused else ACCENT_HOVER,
        )
        self.stop_button.configure(
            text="▶  RESUME CONTROL" if paused else "■  EMERGENCY STOP",
            fg_color=GREEN if paused else RED, hover_color=GREEN if paused else RED,
        )
        if locked:
            self.lock_badge.pack(side="right", padx=8, before=self.camera_button)
        else:
            self.lock_badge.pack_forget()
        self._refresh_mini()
        self._sync_menu_bar()
        if paused:
            self._set_status_row("Safety", "Stopped", RED)
        elif locked:
            self._set_status_row("Safety", "Locked", AMBER)
        else:
            self._set_status_row("Safety", "Running", GREEN)

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self.dashboard = self._build_dashboard()
        self.dashboard.grid(row=1, column=1, sticky="nsew")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=PANEL, height=64, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_propagate(False)
        header.pack_propagate(False)

        label(header, "✋", 26, color=ACCENT).pack(side="left", padx=(20, 10))
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left", pady=8)
        label(brand, "TouchlessX", 19, True).pack(anchor="w")
        label(brand, "Control Your World with Hand Gestures", 11, color=TEXT_DIM).pack(anchor="w")

        self.pause_button = ctk.CTkButton(
            header, text="❚❚  Pause", width=110, height=34, corner_radius=8, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ON_ACCENT, font=ctk.CTkFont(size=12, weight="bold"),
            command=self.toggle_pause,
        )
        self.pause_button.pack(side="right", padx=(8, 20))
        self.camera_button = button(header, "Stop Camera", self._toggle_tracking, "ghost", 120)
        self.camera_button.pack(side="right", padx=8)
        self.face_badge = label(header, "🙂  FACE CONTROL", 11, True, color=GREEN, fg_color=PANEL_ALT,
                                corner_radius=8, width=130, height=30)  # packed only while Face Control is on
        self.practice_badge = label(header, "PRACTICE MODE", 11, True, color=AMBER, fg_color=PANEL_ALT,
                                    corner_radius=8, width=120, height=30)  # packed only while practising
        # Shown only while gesture control is locked; clicking it unlocks without any gesture.
        self.update_badge = ctk.CTkButton(  # packed only when a newer release exists
            header, text="⬆  Update", width=120, height=30, corner_radius=8, fg_color=GREEN, hover_color=GREEN,
            text_color=("#ffffff", "#06281c"), font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda: webbrowser.open((self.update_info or {}).get("url") or RELEASES_PAGE))
        self.voice_badge = label(header, "🎙  Starting…", 11, True, color=TEXT_DIM, fg_color=PANEL_ALT,
                                 corner_radius=8, width=126, height=30)  # packed only while voice is on
        self.lock_badge = ctk.CTkButton(
            header, text="🔒  LOCKED · click to unlock", width=190, height=30, corner_radius=8, fg_color=AMBER,
            hover_color=AMBER, text_color=("#ffffff", "#1a1200"), font=ctk.CTkFont(size=11, weight="bold"),
            command=self.unlock)
        self.fps_label = label(header, "FPS --", 12, True, fg_color=PANEL_ALT, corner_radius=8, width=70, height=30)
        self.fps_label.pack(side="right", padx=8)
        camera = ctk.CTkFrame(header, fg_color="transparent")
        camera.pack(side="right", padx=12)
        self.camera_status_label = label(camera, "● Starting camera…", 12, True, color=TEXT_DIM)
        self.camera_status_label.pack(anchor="e")
        self.camera_detail_label = label(camera, "", 10, color=TEXT_DIM)
        self.camera_detail_label.pack(anchor="e")
        self.profile_menu = option_menu(header, self.profiles.names(), self.config_data["active_profile"],
                                        self.switch_profile, 150)
        self.profile_menu.pack(side="right", padx=(8, 4))
        label(header, "Profile", 11, color=TEXT_DIM).pack(side="right")

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=PANEL, width=230, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.pack_propagate(False)

        self._nav: dict[str, tuple] = {}
        for i, (name, subtitle, glyph) in enumerate(NAV_ITEMS):
            item = ctk.CTkFrame(sidebar, fg_color="transparent", corner_radius=10, height=40)
            item.pack(fill="x", padx=12, pady=(8 if i == 0 else 1, 1))
            item.pack_propagate(False)
            icon = label(item, glyph, 17, color=TEXT_DIM, width=30)
            icon.pack(side="left", padx=(10, 6))
            text = ctk.CTkFrame(item, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True)
            title = label(text, name, 13, True, color=TEXT_DIM, anchor="w", height=18)
            title.pack(anchor="w", pady=(5, 0))
            sub = label(text, subtitle, 10, color=TEXT_DIM, anchor="w", height=14)
            sub.pack(anchor="w")
            _bind_click(item, lambda n=name: self.show_page(n))
            self._nav[name] = (item, icon, title, sub)
        self._highlight_nav("Dashboard")

        tip = card(sidebar, fg_color=PANEL_ALT)
        tip.pack(fill="x", padx=12, pady=16, side="bottom")
        label(tip, "Smarter Gestures,\nBetter Control", 13, True, justify="left").pack(anchor="w", padx=14,
                                                                                         pady=(10, 0))
        label(tip, "Processed on this computer.\nSpace or Esc pauses control.", 11,
              color=TEXT_DIM, justify="left").pack(anchor="w", padx=14, pady=(2, 10))
        # The card is decoration: on a short window it steps aside so it never covers the menu.
        self._sidebar_tip = tip
        sidebar.bind("<Configure>", lambda _e: self.after_idle(self._fit_sidebar_tip))

    def _fit_sidebar_tip(self) -> None:
        tip, last = self._sidebar_tip, list(self._nav.values())[-1][0]
        room = tip.master.winfo_height() - (last.winfo_y() + last.winfo_height())
        needed = tip.winfo_reqheight() + 40
        if room < needed and tip.winfo_ismapped():
            tip.pack_forget()
        elif room >= needed + 20 and not tip.winfo_ismapped():
            tip.pack(fill="x", padx=12, pady=16, side="bottom")

    def current_page_widget(self):
        return self._pages.get(self._current_page)

    def _highlight_nav(self, selected: str) -> None:
        for name, (item, icon, title, sub) in self._nav.items():
            active = name == selected
            item.configure(fg_color=ACCENT if active else "transparent")
            icon.configure(text_color=ON_ACCENT if active else TEXT_DIM)
            title.configure(text_color=ON_ACCENT if active else TEXT_DIM)
            sub.configure(text_color=ON_ACCENT if active else TEXT_DIM)

    def show_page(self, name: str) -> None:
        current = self.dashboard if self._current_page == "Dashboard" else self._pages.get(self._current_page)
        if current is not None:
            current.grid_remove()
        if self._current_page == "Training Studio" and name != "Training Studio":
            self.stop_recording()
        self._current_page = name
        self._highlight_nav(name)
        self._update_dry_run()
        self._sync_drag()
        if name == "Dashboard":
            self.dashboard.grid()
            return
        page = self._pages.get(name)
        if page is None:
            page = self._pages[name] = PAGES[name](self)
            page.grid(row=1, column=1, sticky="nsew")
        else:
            page.grid()
            page.on_show()

    # ------------------------------------------------------------- dashboard
    def _build_dashboard(self) -> ctk.CTkFrame:
        dash = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        dash.grid_columnconfigure(0, weight=1)
        dash.grid_columnconfigure(1, weight=0)
        dash.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(dash, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self.video_panel = card(left)
        self.video_panel.grid(row=0, column=0, sticky="nsew")
        self.video_panel.pack_propagate(False)  # the grid sizes the panel, not the frame inside it
        self.video_label = ctk.CTkLabel(self.video_panel, text="Starting camera…", text_color=TEXT_DIM,
                                        font=ctk.CTkFont(size=13), justify="center")
        self.video_label.pack(fill="both", expand=True, padx=8, pady=8)
        self.live_chip = label(self.video_panel, "●  Starting…", 12, True, color=TEXT_DIM, fg_color=PANEL_ALT,
                               corner_radius=8, height=30, width=130)
        self.live_chip.place(x=18, y=18)
        self.video_fps_chip = label(self.video_panel, "FPS --", 12, True, fg_color=PANEL_ALT, corner_radius=8,
                                    height=30, width=70)
        self.video_fps_chip.place(relx=1.0, x=-18, y=18, anchor="ne")

        cards = ctk.CTkFrame(left, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        cards.grid_columnconfigure((0, 1, 2), weight=1, uniform="info")
        self._build_info_cards(cards)

        features = ctk.CTkFrame(left, fg_color="transparent")
        features.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for col, (key, description, page) in enumerate(FEATURE_CARDS):
            features.grid_columnconfigure(col, weight=1, uniform="feature")
            _, title, glyph, tint = next(c for c in CATEGORIES if c[0] == key)
            item = card(features, corner_radius=12)
            item.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 0 if col == 4 else 5))
            head = ctk.CTkFrame(item, fg_color="transparent")
            head.pack(fill="x", padx=12, pady=(7, 0))
            label(head, glyph, 15, color=tint, height=20).pack(side="left")
            label(head, title, 12, True, height=20).pack(side="left", padx=(6, 0))
            label(item, description + "  ›", 10, color=TEXT_DIM, height=16).pack(anchor="w", padx=12, pady=(0, 7))
            _bind_click(item, lambda p=page: self.show_page(p))

        bottom = ctk.CTkFrame(left, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bottom.grid_columnconfigure((0, 1), weight=1, uniform="bottom")
        self._build_analytics(bottom)
        self._build_guide(bottom)

        self._build_right_panel(dash)
        return dash

    def _build_info_cards(self, parent) -> None:
        gesture = card(parent)
        gesture.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.gesture_glyph = label(gesture, "–", 26, color=ACCENT, width=46)
        self.gesture_glyph.pack(side="left", padx=(14, 6), pady=12)
        text = ctk.CTkFrame(gesture, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        label(text, "Current Gesture", 11, color=TEXT_DIM).pack(anchor="w")
        self.gesture_card = label(text, "—", 18, True)
        self.gesture_card.pack(anchor="w")
        self.active_badge = label(gesture, "Idle", 11, True, color=TEXT_DIM, fg_color=PANEL_ALT,
                                  corner_radius=8, width=60, height=24)
        self.active_badge.pack(side="right", padx=12)

        confidence = card(parent)
        confidence.grid(row=0, column=1, sticky="nsew", padx=6)
        label(confidence, "Confidence", 11, color=TEXT_DIM).pack(anchor="w", padx=16, pady=(10, 0))
        row = ctk.CTkFrame(confidence, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.confidence_card = label(row, "0%", 18, True, width=56, anchor="w")
        self.confidence_card.pack(side="left")
        self.confidence_bar = ctk.CTkProgressBar(row, height=8, progress_color=GREEN, fg_color=TRACK)
        self.confidence_bar.set(0)
        self.confidence_bar.pack(side="left", fill="x", expand=True, padx=(8, 0))

        action = card(parent)
        action.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        label(action, "⚡", 24, color=AMBER, width=40).pack(side="left", padx=(14, 6))
        text = ctk.CTkFrame(action, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        label(text, "Last Action", 11, color=TEXT_DIM).pack(anchor="w")
        self.action_card = label(text, "No action", 18, True)
        self.action_card.pack(anchor="w")

    def _build_analytics(self, parent) -> None:
        box = card(parent, height=174)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        box.pack_propagate(False)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(8, 2))
        label(head, "Gesture Analytics", 14, True).pack(side="left")
        label(head, "This session", 11, color=TEXT_DIM).pack(side="right")
        body = ctk.CTkFrame(box, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.pack(side="right", padx=(8, 4))
        self._stat_labels: dict[str, ctk.CTkLabel] = {}
        for key, title in (("total", "Total Gestures"), ("accuracy", "Recognised Poses"),
                           ("latency", "Avg. Processing Time")):
            label(stats, title, 10, color=TEXT_DIM, height=14).pack(anchor="w")
            value = label(stats, "—", 15, True, height=22)
            value.pack(anchor="w", pady=(0, 4))
            self._stat_labels[key] = value
        self.chart = tk.Canvas(body, bg=resolve(PANEL), highlightthickness=0, height=120)
        self.chart.pack(side="left", fill="both", expand=True)

    def _build_guide(self, parent) -> None:
        box = card(parent, height=174)
        box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        box.pack_propagate(False)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(8, 2))
        label(head, "Gesture Guide", 14, True).pack(side="left")
        edit = label(head, "Customize", 11, color=ACCENT, cursor="hand2")
        edit.pack(side="right")
        edit.bind("<Button-1>", lambda _e: self.show_page("Gesture Studio"))
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._guide_labels: dict[str, ctk.CTkLabel] = {}
        for i, gesture in enumerate(GUIDE_GESTURES):
            grid.grid_columnconfigure(i % 4, weight=1, uniform="guide")
            grid.grid_rowconfigure(i // 4, weight=1)
            cell = ctk.CTkFrame(grid, fg_color=PANEL_ALT, corner_radius=10)
            cell.grid(row=i // 4, column=i % 4, sticky="nsew", padx=3, pady=3)
            label(cell, gesture_glyph(gesture), 17, height=20).pack(pady=(3, 0))
            label(cell, gesture_label(gesture), 11, True, height=14).pack()
            action = label(cell, "", 10, color=TEXT_DIM, height=14)
            action.pack(pady=(0, 3))
            self._guide_labels[gesture] = action

    def _refresh_guide(self) -> None:
        for gesture, action in self._guide_labels.items():
            action.configure(text=action_label(self.action_controller.action_for(gesture)))

    # ----------------------------------------------------------- right panel
    def _build_right_panel(self, parent) -> None:
        right = ctk.CTkScrollableFrame(parent, fg_color="transparent", width=320)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 6), pady=16)

        self.stop_button = ctk.CTkButton(
            right, text="■  EMERGENCY STOP", height=46, corner_radius=12, fg_color=RED, hover_color=RED,
            text_color=ON_ACCENT, font=ctk.CTkFont(size=15, weight="bold"), command=self.emergency_stop)
        self.stop_button.pack(fill="x", padx=4, pady=(0, 14))
        label(right, "Quick Controls", 15, True).pack(anchor="w", pady=(0, 8))
        grid = ctk.CTkFrame(right, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="quick")
        self._quick_status: dict[str, ctk.CTkLabel] = {}
        for i, (key, title, glyph, tint) in enumerate(CATEGORIES):
            item = card(grid, corner_radius=12)
            item.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
            head = ctk.CTkFrame(item, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 0))
            label(head, glyph, 16, color=tint, width=22).pack(side="left")
            label(head, title, 12, True).pack(side="left", padx=(6, 0))
            foot = ctk.CTkFrame(item, fg_color="transparent")
            foot.pack(fill="x", padx=10, pady=(2, 8))
            status = label(foot, "Enabled", 11, color=GREEN)
            status.pack(side="left")
            switch(foot, self.toggle_vars[key]).pack(side="right")
            self._quick_status[key] = status

        label(right, "Recent Gestures", 15, True).pack(anchor="w", pady=(16, 8))
        recent = card(right)
        recent.pack(fill="x", padx=4)
        self._recent_empty = label(recent, "No gestures yet.", 12, color=TEXT_DIM)
        self._recent_empty.pack(padx=14, pady=12, anchor="w")
        self._recent_rows = []
        for _ in range(self._recent.maxlen):
            row = ctk.CTkFrame(recent, fg_color="transparent")
            glyph = label(row, "", 16, width=30)
            glyph.pack(side="left")
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", padx=(6, 0))
            title = label(text, "", 12, True, height=16)
            title.pack(anchor="w")
            sub = label(text, "", 11, color=TEXT_DIM, height=14)
            sub.pack(anchor="w")
            age = label(row, "", 11, color=TEXT_DIM)
            age.pack(side="right")
            self._recent_rows.append((row, glyph, title, sub, age))

        label(right, "System Status", 15, True).pack(anchor="w", pady=(16, 8))
        status_card = card(right)
        status_card.pack(fill="x", padx=4)
        self.status_rows: dict[str, ctk.CTkLabel] = {}
        for key in ("Mode", "Camera", "Hand Tracking", "AI Model", "System Controls", "Safety", "Latency"):
            row = ctk.CTkFrame(status_card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=4)
            label(row, key, 12, color=TEXT_DIM).pack(side="left")
            value = label(row, "—", 12, True, color=TEXT_DIM)
            value.pack(side="right")
            self.status_rows[key] = value
        ctk.CTkFrame(status_card, fg_color="transparent", height=4).pack()
        self._set_status_row("Safety", "Running", GREEN)

        tip = card(right, fg_color=PANEL_ALT)
        tip.pack(fill="x", padx=4, pady=(12, 4))
        label(tip, "Tip: pinch twice quickly to double-click. Change any gesture in Gesture Studio.", 11,
              color=TEXT_DIM,
              justify="left", wraplength=270).pack(padx=12, pady=10, anchor="w")

    def _set_status_row(self, key: str, text: str, color: str) -> None:
        if self._shown.get("status:" + key) != (text, color):
            self._shown["status:" + key] = (text, color)
            self.status_rows[key].configure(text=text, text_color=color)

    def _set_camera_status(self, text: str, color: str, detail: str = "") -> None:
        self.camera_status_label.configure(text=text, text_color=color)
        self.camera_detail_label.configure(text=detail)

    # ----------------------------------------------------------- background
    def _worker_loop(self) -> None:
        tracker: HandTracker | None = None
        frame_times: deque = deque(maxlen=20)
        misses = 0
        last_label, last_time = "", 0.0

        while not self._stop_event.is_set():
            if self._pending_camera is not None:
                self.camera.stop()
                self.camera, self._pending_camera = self._pending_camera, None
            if not self._tracking_wanted.is_set():
                if self.camera.is_open():
                    self.camera.stop()
                    self.gesture_engine.reset()
                    self.action_controller.release()
                    self._events.put(("stopped",))
                time.sleep(0.05)
                continue
            if not self.camera.is_open():
                try:
                    if tracker is None:
                        tracker = HandTracker(self.model_path)
                    self.camera.start()
                except (CameraError, TrackerError) as exc:
                    self._tracking_wanted.clear()
                    self._events.put(("error", str(exc)))
                    continue
                misses = 0
                frame_times.clear()
                self._events.put(("started",))

            frame = self.camera.read()
            if frame is None:
                misses += 1
                if misses > 150:
                    self.camera.stop()
                    self._tracking_wanted.clear()
                    self._events.put(("error", "The camera stopped delivering frames."))
                time.sleep(0.01)
                continue
            misses = 0
            started = time.monotonic()
            frame_times.append(started)

            face = None
            if self._face_mode:
                result = None
                face, event, dispatch = self._face_step(frame, started)
            else:
                tracker.preferred_hand = self._preferred_hand
                result = tracker.process(frame)
                event = self.gesture_engine.update(result, started)
                if self._current_page == "Learn":
                    self._events.put(("lesson", event, started))  # every frame: a flick lasts only one
                try:
                    dispatch = self.action_controller.dispatch(event)
                except Exception:
                    dispatch = None  # an OS input hiccup must not take tracking down with it
            latency_ms = (time.monotonic() - started) * 1000.0

            fps = 0.0
            if len(frame_times) >= 2:
                fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0] + 1e-6)
            self.stats.record_frame(event, latency_ms, fps, started)
            if result is not None and not self.action_controller.dry_run:
                self.frame_log.record(event, self.gesture_engine.last_raw, result.landmarks, fps,
                                      pinch_distance(result.landmarks), dispatch.label if dispatch else "")

            if dispatch is not None:
                # A held pose dispatches every frame; report it once per run, not 30x a second.
                held = event.name in self.gesture_engine.continuous_gestures
                repeat = held and dispatch.label == last_label and started - last_time < 1.5
                last_label, last_time = dispatch.label, started
                if not repeat:
                    self._events.put(("action", event.name, dispatch.label, event.confidence))

            rec = self._recording
            if rec is not None and not rec["done"] and not rec["paused"]:
                if result is not None and started >= rec["start"] and started - rec["last"] >= 0.05:
                    rec["samples"].append(result.landmarks.copy())
                    rec["last"] = started
                if len(rec["samples"]) >= _RECORD_LIMIT:
                    rec["done"] = True
                    self._events.put(("recorded",))

            image = None
            if self._current_page in PREVIEW_PAGES:
                if result is not None and self._show_landmarks:
                    tracker.draw(frame, result)
                    if result.other is not None:
                        tracker.draw(frame, result.other)
                if face is not None and self._show_landmarks:
                    FaceTracker.draw(frame, face, self.last_face_event, self.face_engine.wink_threshold)
                guide = self.action_controller.scroll_guide(event)
                if guide is not None:
                    # The neutral zone of glide scrolling: hand inside = stopped, outside = scrolling.
                    h, w = frame.shape[:2]
                    centre = (int(guide[0] * w), int(guide[1] * h))
                    colour = (60, 170, 255) if guide[3] else (200, 200, 200)
                    cv2.ellipse(frame, centre, (int(guide[2] * w), int(guide[2] * h)), 0, 0, 360, colour, 2, cv2.LINE_AA)
                    if guide[3]:
                        palm = (int(event.palm[0] * w), int(event.palm[1] * h))
                        cv2.arrowedLine(frame, centre, palm, colour, 2, cv2.LINE_AA, tipLength=0.2)
                if not self._mirror_preview:
                    frame = cv2.flip(frame, 1)  # display only: tracking always uses the mirrored frame
                # The frame only ever lives in memory: it is shown, then dropped.
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            with self._latest_lock:
                self._latest = (image, event, fps)

        self.frame_log.flush()
        self.camera.stop()
        if tracker is not None:
            tracker.close()

    # ------------------------------------------------------------ main loop
    def _poll(self) -> None:
        try:
            while True:
                self._handle_event(*self._events.get_nowait())
        except queue.Empty:
            pass
        if not self._camera_running:
            self.action_controller.tick()  # a voice-started scroll keeps moving without camera frames
        with self._latest_lock:
            latest, self._latest = self._latest, None
        if latest is not None and self._camera_running:
            image, event, fps = latest
            self.last_event = event
            if image is not None and self.state() != "iconic":
                page = self._pages.get(self._current_page)
                if self._current_page == "Dashboard":
                    self._render_frame(image, self.video_panel, self.video_label)
                elif hasattr(page, "preview_label") and page.preview_label.winfo_exists():
                    self._render_frame(image, page.preview_panel, page.preview_label)
            self._update_live_panels(event, fps)
        self.after(15, self._poll)

    def _handle_event(self, kind: str, *data) -> None:
        if kind == "started":
            self._camera_running = True
            self._auto_retries = 0
            config = self.config_data
            self._set_camera_status("● Camera Connected", GREEN,
                                    f"Camera {config['camera_index']} · {config['frame_width']}×{config['frame_height']}")
            self.camera_button.configure(text="Stop Camera")
            self.live_chip.configure(text="●  Live Tracking", text_color=GREEN)
            self._set_status_row("Camera", "Connected", GREEN)
            self._set_status_row("AI Model", "Loaded", GREEN)
            self._shown.pop("mini", None)
            self._refresh_mini()
        elif kind == "stopped":
            self._camera_off("Camera stopped.\n\nPress Start Camera to resume tracking.", "● Camera Off", TEXT_DIM,
                             "Off")
        elif kind == "error":
            message = data[0]
            retrying = self._auto_retries < 10
            if retrying:
                # e.g. the OS camera-permission prompt is still open: try again once it is answered
                self._auto_retries += 1
                self.after(3000, self._retry_tracking)
            self._camera_off(
                f"Camera unavailable\n\n{message}\n\n"
                + ("Retrying automatically…" if retrying else "Fix the problem, then press Start Camera."),
                "● Camera Error", RED, "Error",
            )
        elif kind == "action":
            gesture, action, confidence = data
            if self.action_controller.dry_run:
                action = f"Practice: {action}"  # nothing was sent, so nothing is logged
            else:
                self.action_log.record(gesture, action, confidence)
                self.history.record(gesture, action, confidence)
            self._recent.appendleft((time.monotonic(), gesture, action))
            self.action_card.configure(text=action)
            self._refresh_recent()
            self._sync_pause_ui()  # a gesture may have toggled pause
        elif kind == "lesson":
            page = self._pages.get("Learn")
            if page is not None and self._current_page == "Learn":
                page.on_lesson_event(*data)
        elif kind == "toggle_keyboard":
            self.toggle_keyboard()
        elif kind == "key_flash":
            if self.keyboard_window is not None and self.keyboard_window.winfo_exists():
                self.keyboard_window.flash(data[0])
        elif kind == "face_error":
            self.face_error = data[0]  # shown on the Face Control page; hand control carries on
            self.set_face_control(False)
            page = self._pages.get("Face Control")
            if page is not None:
                page.rebuild()
        elif kind == "recorded":
            self.stop_recording()
        elif kind == "update_checked":
            self.update_info = data[0]
            if data[0]["status"] == "update":
                self.update_badge.configure(text=f"⬆  Update {data[0]['latest']}")
                self.update_badge.pack(side="right", padx=8, before=self.camera_button)
            if data[1] is not None:
                data[1](data[0])
        elif kind == "voice_heard":
            self._on_voice_heard(data[0], data[1])
        elif kind == "assistant_done":
            self._finish_task(data[0], data[1])
        elif kind == "voice_state":
            if self.voice is not None:
                self.voice_status = (data[0], data[1])
                self._sync_voice_ui()
        elif kind == "paste_clip":
            self._paste_clip(data[0])
        elif kind == "own_window":
            if data[0] == "minimize":
                self.iconify()
            else:
                self.show_window()
        elif kind == "show_window":
            self.show_window()
            self.show_page("Dashboard")

    def _retry_tracking(self) -> None:
        if not self._camera_running and self._auto_retries < 99:
            self.start_tracking()

    def _camera_off(self, message: str, status: str, color: str, row_text: str) -> None:
        self._camera_running = False
        self.stop_recording()
        # CTkLabel ignores image=None once an image is set, so swap in a blank one.
        self.video_label.configure(image=self._blank_image, text=message)
        self.video_label.image = self._blank_image
        self._set_camera_status(status, color)
        self.camera_button.configure(text="Start Camera")
        self.live_chip.configure(text="●  Not tracking", text_color=color)
        self.fps_label.configure(text="FPS --")
        self.video_fps_chip.configure(text="FPS --")
        self._set_status_row("Camera", row_text, color)
        self._set_status_row("Hand Tracking", "Idle", TEXT_DIM)
        self._shown.pop("fps", None)
        self.last_event = None
        self._refresh_mini("–")

    def _render_frame(self, image: Image.Image, panel, target) -> None:
        # winfo sizes are in scaled pixels, CTkImage sizes in unscaled units.
        scale = self._get_window_scaling()
        available_w = max(160, int(panel.winfo_width() / scale) - 20)
        available_h = max(90, int(panel.winfo_height() / scale) - 20)
        ratio = min(available_w / image.width, available_h / image.height)
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        target.configure(image=ctk_image, text="")
        target.image = ctk_image  # keep a reference - CTkLabel won't hold one for us

    def _show(self, key: str, widget, **options) -> None:
        """configure() only when the value changed - redrawing CTk widgets is not free."""
        if self._shown.get(key) != options:
            self._shown[key] = options
            widget.configure(**options)

    def _update_live_panels(self, event, fps: float) -> None:
        fps_text = f"FPS {fps:.0f}"
        if self._shown.get("fps") != fps_text:
            self._shown["fps"] = fps_text
            self.fps_label.configure(text=fps_text)
            self.video_fps_chip.configure(text=fps_text)

        has_hand = event.name not in ("none", "resting")
        if event.name == "resting":
            self._set_status_row("Hand Tracking", "Resting", AMBER)
        else:
            self._set_status_row("Hand Tracking", "Active" if has_hand else "Idle", GREEN if has_hand else TEXT_DIM)
        self._show("gesture", self.gesture_card, text=gesture_label(event.name))
        self._show("glyph", self.gesture_glyph, text=gesture_glyph(event.name))
        recognised = has_hand and event.name != "unknown"
        self._show("badge", self.active_badge, text="Active" if recognised else "Idle",
                   text_color=GREEN if recognised else TEXT_DIM)
        percent = int(round(event.confidence))
        if self._shown.get("confidence") != percent:
            self._shown["confidence"] = percent
            self.confidence_card.configure(text=f"{percent}%")
            self.confidence_bar.set(percent / 100.0)
        self._sync_pause_ui()
        self._refresh_mini(gesture_label(event.name))

    def _refresh_mini(self, gesture_text: str | None = None) -> None:
        if gesture_text is None:
            gesture_text = self._mini_gesture
        self._mini_gesture = gesture_text
        if self.mini is None:
            return
        state = (self.safety.paused, self._camera_running, self.action_controller.dry_run, gesture_text,
                 self.safety.locked)
        if self._shown.get("mini") != state:
            self._shown["mini"] = state
            self.mini.refresh(*state)

    def _refresh_recent(self) -> None:
        if self._recent and self._recent_empty.winfo_ismapped():
            self._recent_empty.pack_forget()
        now = time.monotonic()
        for (row, glyph, title, sub, age), entry in zip(self._recent_rows, self._recent):
            timestamp, gesture, action = entry
            glyph.configure(text=gesture_glyph(gesture))
            title.configure(text=gesture_label(gesture))
            sub.configure(text=action)
            seconds = int(now - timestamp)
            age.configure(text=f"{seconds}s ago" if seconds < 60 else f"{seconds // 60}m ago")
            if not row.winfo_ismapped():
                row.pack(fill="x", padx=12, pady=5)

    def _draw_chart(self) -> None:
        canvas = self.chart
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 80 or height < 60:
            return
        canvas.delete("all")
        canvas.configure(bg=resolve(PANEL))
        border, dim = resolve(BORDER), resolve(TEXT_DIM)
        left, right, top, bottom = 34, width - 6, 8, height - 18
        for percent in (0, 50, 100):
            y = bottom - (bottom - top) * percent / 100.0
            canvas.create_line(left, y, right, y, fill=border)
            canvas.create_text(left - 6, y, text=f"{percent}%", fill=dim, anchor="e", font=("Helvetica", 9))
        window = self.stats.timeline.maxlen
        canvas.create_text(left, height - 6, text=f"-{window // 60} min", fill=dim, anchor="w",
                           font=("Helvetica", 9))
        canvas.create_text(right, height - 6, text="now", fill=dim, anchor="e", font=("Helvetica", 9))
        canvas.create_text((left + right) / 2, height - 6, text="tracking confidence", fill=dim,
                           font=("Helvetica", 9))
        values = list(self.stats.timeline)
        if len(values) < 2:
            return
        step = (right - left) / (window - 1)
        offset = window - len(values)  # newest sample sits at the right edge
        points = []
        for i, value in enumerate(values):
            points += [left + (offset + i) * step, bottom - (bottom - top) * value / 100.0]
        canvas.create_polygon(points[0], bottom, *points, points[-2], bottom, fill=resolve(ACCENT_SOFT), outline="")
        canvas.create_line(*points, fill=resolve(ACCENT), width=2)

    def _tick(self) -> None:
        stats = self.stats
        self._stat_labels["total"].configure(text=str(stats.total_gestures))
        self._stat_labels["accuracy"].configure(text=f"{stats.accuracy:.0f}%" if stats.hand_frames else "—")
        self._stat_labels["latency"].configure(text=f"{stats.avg_latency_ms:.0f} ms" if stats.frames else "—")
        if self._current_page == "Dashboard":
            self._draw_chart()
            self._refresh_recent()
        self._watch_clipboard()
        self._set_status_row("Mode", self.config_data["active_profile"], TEXT)
        try:
            self.action_controller.refresh_screen()  # resolution or display may have changed
        except Exception:
            pass
        self._set_status_row("Latency", f"{stats.avg_latency_ms:.0f} ms" if stats.frames else "—", TEXT_DIM)
        if self.input_ok():
            self._set_status_row("System Controls", "Ready", GREEN)
        else:
            self._set_status_row("System Controls", "No permission", AMBER)
        self.after(1000, self._tick)

    # ------------------------------------------------------------------ end
    def _on_close(self) -> None:
        """The window's close button: quit, or carry on in the menu bar if so configured."""
        if self.config_data["close_to_menu_bar"] and self.menu_bar is not None:
            self.withdraw()
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self.menu_bar is not None:
            self.menu_bar.remove()
            self.menu_bar = None
        self._stop_event.set()
        self._worker.join(timeout=2.0)  # let the worker release the camera
        self.action_controller.release()
        if self._save_job is not None:
            self.after_cancel(self._save_job)
            self._save_now()
        if self.voice is not None:
            self.voice.stop()
        self.history.close()
        self.destroy()


class MiniController(ctk.CTkToplevel):
    """A small always-on-top window with the state and a Pause button, so control can be
    stopped while another application is in front."""

    def __init__(self, app: App) -> None:
        super().__init__(app, fg_color=PANEL)
        self.app = app
        self.title("TouchlessX")
        self.geometry(f"270x58+{max(0, app.winfo_screenwidth() - 300)}+60")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", lambda: app.set_mini_controller(False))

        self.dot = label(self, "●", 16, color=TEXT_DIM, width=22)
        self.dot.pack(side="left", padx=(10, 4))
        text = ctk.CTkFrame(self, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        self.state_label = label(text, "Starting…", 12, True, anchor="w", height=16)
        self.state_label.pack(anchor="w", pady=(10, 0))
        self.gesture_label = label(text, "", 11, color=TEXT_DIM, anchor="w", height=14)
        self.gesture_label.pack(anchor="w")
        button(self, "Open", app.show_window, "ghost", 52).pack(side="right", padx=(4, 10))
        self.pause_button = button(self, "Pause", app.toggle_pause, "primary", 70)
        self.pause_button.pack(side="right")

    def refresh(self, paused: bool, camera_running: bool, practice: bool, gesture_text: str,
                locked: bool = False) -> None:
        if not camera_running:
            state, color = "Camera off", TEXT_DIM
        elif paused:
            state, color = "Stopped", RED
        elif locked:
            state, color = "Locked", AMBER
        elif practice:
            state, color = "Practice mode", AMBER
        else:
            state, color = "Controlling", GREEN
        self.dot.configure(text_color=color)
        self.state_label.configure(text=state)
        self.gesture_label.configure(text=gesture_text)
        self.pause_button.configure(text="Resume" if paused else "Pause", fg_color=RED if paused else ACCENT,
                                    hover_color=RED if paused else ACCENT_HOVER)

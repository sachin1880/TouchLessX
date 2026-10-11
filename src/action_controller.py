"""Action Dispatcher: converts validated gestures into mouse/keyboard commands.

The actual mouse/keyboard calls are isolated behind the small `InputExecutor`
interface so the mapping/gating logic here can be unit tested with a fake executor,
with no real camera, display, or OS input side effects (see tests/test_mapping.py).
"""
from __future__ import annotations

import math
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import DEFAULT_MAPPINGS
from .gesture_engine import CONTINUOUS_GESTURES, FLEXIBLE_POSES, GestureEvent
from .safety import SafetyGuard

SMART_HOME_PREFIX = "smart_home:"

# Scrolling. Distances are fractions of the camera frame.
GLIDE_DEAD_ZONE = 0.045                 # how far from the neutral point before gliding starts
GLIDE_PIXELS_PER_SECOND = 12000.0       # scaled by (distance beyond the dead zone) ** 1.4
FOLLOW_SLACK = 0.006                    # tracking jitter below this moves nothing
FOLLOW_PIXELS_PER_FRAME_HEIGHT = 3000.0
PIXELS_PER_WHEEL_CLICK = 40.0           # for systems that only take whole wheel clicks
MINIMIZE_MIN_INTERVAL = 2.0             # seconds between two minimise actions
# Swipe ("flick") scrolling. Thresholds come from recorded real use: deliberate swipes
# peaked at 0.8-2.0 frame heights per second, while steering the cursor stayed under 0.5.
FLICK_SPEED = 0.7                       # peak speed a movement must reach to count as a swipe
FLICK_RUN_SPEED = 0.45                  # ...measured over the part of it faster than this
FLICK_MIN_TRAVEL = 0.06                 # ...which must cover at least this much of the frame
FLICK_PIXELS_PER_SPEED = 1900.0         # starting scroll speed (px/s) per frame-height/s of swipe
FLICK_FRICTION_SECONDS = 0.4            # the coasting scroll loses ~63% of its speed in this time
FLICK_RETURN_SECONDS = 1.1              # after a swipe, the opposite direction is the hand coming back
# A fast-moving hand blurs, and the pose is briefly lost ("unknown"). Scrolling carries
# on through such frames for up to this long instead of breaking off.
SCROLL_STICKY_SECONDS = 1.0
VOICE_SCROLL_MAX_SECONDS = 90.0         # a steady voice scroll never runs longer than this unattended
SHORTCUT_PREFIX = "shortcut:"
APP_PREFIX = "app:"


class AppError(ValueError):
    """Raised for something that is not an application TouchlessX may open."""


def validate_app_path(path) -> str:
    """Only real application bundles/executables can be launched, with no arguments
    and never through a shell - "open an app" must not become "run a command"."""
    path = Path(str(path)).expanduser()
    system = platform.system()
    if system == "Darwin":
        ok = path.suffix.lower() == ".app" and path.is_dir()
    elif system == "Windows":
        ok = path.suffix.lower() == ".exe" and path.is_file()
    else:
        raise AppError("Opening applications is supported on macOS and Windows only.")
    if not ok:
        raise AppError("Choose an application (.app on macOS, .exe on Windows).")
    return str(path)

# Keys a user-defined shortcut may contain. Shortcuts are key presses only - there is
# deliberately no way to make a gesture run a command or script.
MODIFIER_KEYS = ("command", "ctrl", "alt", "shift", "win")
ALLOWED_KEYS = frozenset(
    list("abcdefghijklmnopqrstuvwxyz0123456789") + [f"f{i}" for i in range(1, 13)] + [
        "left", "right", "up", "down", "space", "enter", "tab", "esc", "backspace", "delete",
        "home", "end", "pageup", "pagedown", "-", "=", "[", "]", ",", ".", "/", ";", "'", "`",
    ]
)
_KEY_ALIASES = {
    "cmd": "command", "⌘": "command", "control": "ctrl", "option": "alt", "opt": "alt", "windows": "win",
    "super": "win", "escape": "esc", "return": "enter", "del": "delete", "spacebar": "space",
    "pgup": "pageup", "pgdn": "pagedown", "plus": "=",
}


class ShortcutError(ValueError):
    """Raised for a shortcut that isn't made of approved keys."""


def parse_shortcut(text) -> list[str]:
    """'Cmd+Shift+T' (or a list of key names) -> ['command', 'shift', 't'], validated
    against the allowlist: any number of modifiers plus exactly one ordinary key."""
    parts = text if isinstance(text, (list, tuple)) else str(text).replace(" ", "").split("+")
    keys = [_KEY_ALIASES.get(str(part).lower(), str(part).lower()) for part in parts if str(part)]
    modifiers = [k for k in keys if k in MODIFIER_KEYS]
    others = [k for k in keys if k not in MODIFIER_KEYS]
    if len(others) != 1 or len(set(modifiers)) != len(modifiers):
        raise ShortcutError("Use modifiers plus one key, e.g. Cmd+Shift+T or Ctrl+Alt+Right.")
    if others[0] not in ALLOWED_KEYS:
        raise ShortcutError(f"'{others[0]}' is not an allowed key.")
    return [m for m in MODIFIER_KEYS if m in modifiers] + others


def shortcut_text(keys: list[str]) -> str:
    return " + ".join(k.capitalize() if len(k) > 1 else k.upper() for k in keys)


class InputExecutor(Protocol):
    def move_mouse(self, x: int, y: int) -> None: ...
    def click(self, button: str = "left") -> None: ...
    def double_click(self) -> None: ...
    def click_again(self) -> None: ...
    def press_key(self, key: str) -> None: ...
    def hotkey(self, keys: list[str]) -> None: ...
    def scroll(self, clicks: int) -> None: ...
    def hscroll(self, clicks: int) -> None: ...
    def scroll_pixels(self, dx: int, dy: int) -> None: ...
    def minimize_window(self) -> bool: ...
    def restore_window(self) -> bool: ...
    def open_app(self, path: str) -> None: ...
    def mouse_down(self) -> None: ...
    def mouse_up(self) -> None: ...


# pyautogui's macOS backend has no working mapping for media keys ("playpause" maps to
# nothing, the volume keys to codes modern macOS ignores) - they have to be posted as
# system-defined "special key" events instead.
_MAC_SPECIAL_KEYS = {
    "playpause": "KEYTYPE_PLAY",
    "nexttrack": "KEYTYPE_NEXT",
    "prevtrack": "KEYTYPE_PREVIOUS",
    "volumeup": "KEYTYPE_SOUND_UP",
    "volumedown": "KEYTYPE_SOUND_DOWN",
    "volumemute": "KEYTYPE_MUTE",
}


class PyAutoGuiExecutor:
    """Default executor - drives the real mouse/keyboard via pyautogui."""

    def __init__(self) -> None:
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
        self._pyautogui = pyautogui
        self.screen_size = tuple(pyautogui.size())
        self._minimized: list[tuple] = []  # (app, window) pairs this executor minimised, newest last
        # Set by the UI: called with "minimize" / "restore" when the window in front is
        # TouchlessX's own. See `minimize_window` for why that case can't be handled here.
        self.own_window_handler = None
        self._wheel_remainder = [0.0, 0.0]
        self._mac = None
        if platform.system() == "Darwin":
            try:
                from pyautogui import _pyautogui_osx

                self._mac = _pyautogui_osx
            except Exception:
                self._mac = None

    def refresh_screen_size(self) -> tuple[int, int]:
        """Re-reads the main display's size (it changes with resolution or display swaps)."""
        self.screen_size = tuple(self._pyautogui.size())
        return self.screen_size

    def move_mouse(self, x: int, y: int) -> None:
        self._pyautogui.moveTo(x, y, _pause=False)

    def click(self, button: str = "left") -> None:
        self._pyautogui.click(button=button)

    def double_click(self) -> None:
        self._pyautogui.doubleClick()

    def click_again(self) -> None:
        """The second click of a double-click, following a `click()` moments earlier.
        Windows and X11 pair two quick clicks themselves; macOS only does so when the
        event is explicitly marked as click number two."""
        if self._mac is None:
            self._pyautogui.click()
            return
        import Quartz

        position = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        for event_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(None, event_type, position, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, 2)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def press_key(self, key: str) -> None:
        special = _MAC_SPECIAL_KEYS.get(key) if self._mac is not None else None
        if special is not None:
            self._mac._specialKeyEvent(special, "down")
            self._mac._specialKeyEvent(special, "up")
            return
        self._pyautogui.press(key)

    def hotkey(self, keys: list[str]) -> None:
        self._pyautogui.hotkey(*keys)

    def scroll(self, clicks: int) -> None:
        self._pyautogui.scroll(clicks)

    def hscroll(self, clicks: int) -> None:
        """Horizontal scroll, positive = towards the right."""
        if self._mac is None:
            self._pyautogui.hscroll(clicks)
            return
        # Posted directly: pyautogui's macOS hscroll loses the sign of negative amounts.
        # Quartz counts a positive horizontal wheel value as "left".
        import Quartz

        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 0, -int(clicks))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def scroll_pixels(self, dx: int, dy: int) -> None:
        """Smooth scrolling by pixels: +dx = right, +dy = up."""
        if self._mac is not None:
            import Quartz

            # Pixel units give continuous motion instead of jumping a line at a time.
            # Quartz counts a positive horizontal value as "left".
            event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 2, int(dy), -int(dx))
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            return
        # Elsewhere only whole wheel clicks exist: carry the fraction until it adds up.
        self._wheel_remainder[0] += dx / PIXELS_PER_WHEEL_CLICK
        self._wheel_remainder[1] += dy / PIXELS_PER_WHEEL_CLICK
        for i, send in ((0, self._pyautogui.hscroll), (1, self._pyautogui.scroll)):
            clicks = int(self._wheel_remainder[i])
            if clicks:
                self._wheel_remainder[i] -= clicks
                send(clicks)

    def minimize_window(self) -> bool:
        """Minimises the window in front and remembers it for `restore_window`."""
        if self._mac is None:
            self._pyautogui.hotkey("win", "down")
            return True
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return False
        if app.processIdentifier() == os.getpid():
            # Our own window. The accessibility call would run AppKit's minimise inside
            # this process on the tracking thread, and AppKit aborts the process for
            # window work off the main thread - so hand it to the UI thread instead.
            if self.own_window_handler is None:
                return False
            self.own_window_handler("minimize")
            self._minimized.append((None, None))
            return True
        return self._minimize_app_window(app)

    def _minimize_app_window(self, app) -> bool:
        import ApplicationServices as AS

        element = AS.AXUIElementCreateApplication(app.processIdentifier())
        error, window = AS.AXUIElementCopyAttributeValue(element, "AXFocusedWindow", None)
        if error or window is None:
            error, window = AS.AXUIElementCopyAttributeValue(element, "AXMainWindow", None)
        if error or window is None:
            return False
        if AS.AXUIElementSetAttributeValue(window, "AXMinimized", True):
            return False  # non-zero = AXError (e.g. the window can't be minimised)
        self._minimized.append((app, window))
        del self._minimized[:-10]
        return True

    def restore_window(self) -> bool:
        """Brings back the window most recently minimised by `minimize_window`."""
        if self._mac is None:
            self._pyautogui.hotkey("alt", "tab")
            return True
        import ApplicationServices as AS

        if not self._minimized:
            return False
        app, window = self._minimized.pop()
        if app is None:  # TouchlessX's own window: restored by the UI thread
            if self.own_window_handler is None:
                return False
            self.own_window_handler("restore")
            return True
        if app.processIdentifier() == os.getpid():
            return False  # never touch our own windows through accessibility from this thread
        if AS.AXUIElementSetAttributeValue(window, "AXMinimized", False):
            return False  # the window was closed in the meantime
        app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
        AS.AXUIElementPerformAction(window, "AXRaise")
        return True

    def open_app(self, path: str) -> None:
        path = validate_app_path(path)
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", path])
        else:
            os.startfile(path)  # type: ignore[attr-defined]

    def mouse_down(self) -> None:
        self._pyautogui.mouseDown()

    def mouse_up(self) -> None:
        self._pyautogui.mouseUp()

    def has_permission(self) -> bool:
        """macOS silently drops synthetic input unless the launching app has been
        granted Accessibility access; other platforms need no such grant."""
        if self._mac is None:
            return True
        try:
            import Quartz

            return bool(Quartz.CGPreflightPostEventAccess())
        except Exception:
            return True

    def request_permission(self) -> None:
        if self._mac is None:
            return
        try:
            import Quartz

            Quartz.CGRequestPostEventAccess()
        except Exception:
            pass


class NullExecutor:
    """Fallback when pyautogui can't initialize (no display, missing OS input/
    accessibility permission, headless environment, ...). The app keeps running and
    showing detection - it just doesn't move the mouse or send key presses."""

    def __init__(self) -> None:
        self.screen_size = (1920, 1080)

    def move_mouse(self, x: int, y: int) -> None:
        pass

    def click(self, button: str = "left") -> None:
        pass

    def double_click(self) -> None:
        pass

    def click_again(self) -> None:
        pass

    def press_key(self, key: str) -> None:
        pass

    def hotkey(self, keys: list[str]) -> None:
        pass

    def scroll(self, clicks: int) -> None:
        pass

    def hscroll(self, clicks: int) -> None:
        pass

    def scroll_pixels(self, dx: int, dy: int) -> None:
        pass

    def minimize_window(self) -> bool:
        return True

    def restore_window(self) -> bool:
        return True

    def open_app(self, path: str) -> None:
        pass

    def mouse_down(self) -> None:
        pass

    def mouse_up(self) -> None:
        pass

    def has_permission(self) -> bool:
        return False

    def request_permission(self) -> None:
        pass


class _OneEuroFilter:
    """Speed-adaptive low-pass filter (Casiez et al., "1 Euro Filter"): heavy smoothing
    while the hand is nearly still, so the cursor doesn't tremble, and almost none
    while it moves fast, so the cursor doesn't lag behind."""

    def __init__(self) -> None:
        self._value: float | None = None
        self._rate = 0.0
        self._time = 0.0

    def reset(self) -> None:
        self._value = None

    @staticmethod
    def _alpha(cutoff_hz: float, dt: float) -> float:
        return 1.0 / (1.0 + 1.0 / (2.0 * math.pi * cutoff_hz * dt))

    def __call__(self, value: float, now: float, min_cutoff: float, beta: float) -> float:
        if self._value is None:
            self._value, self._rate, self._time = value, 0.0, now
            return value
        dt = max(1e-3, now - self._time)
        self._time = now
        rate = (value - self._value) / dt
        self._rate += self._alpha(1.0, dt) * (rate - self._rate)
        cutoff = min_cutoff + beta * abs(self._rate)
        self._value += self._alpha(cutoff, dt) * (value - self._value)
        return self._value


@dataclass
class DispatchResult:
    label: str
    category: str


@dataclass(frozen=True)
class ActionSpec:
    label: str
    category: str  # config "enabled" key that must be on ("" = always allowed)
    kind: str  # "continuous" (re-fires while a pose is held) or "discrete" (fires once)


ACTIONS: dict[str, ActionSpec] = {
    "none": ActionSpec("No action", "", "any"),
    "move_cursor": ActionSpec("Mouse Move", "mouse_control", "continuous"),
    "scroll": ActionSpec("Scroll (follow hand)", "mouse_control", "continuous"),
    "volume": ActionSpec("Volume Up / Down", "volume_control", "continuous"),
    "safety_hold": ActionSpec("Safety Hold (do nothing)", "", "continuous"),
    "left_click": ActionSpec("Left Click", "mouse_control", "discrete"),
    "right_click": ActionSpec("Right Click", "mouse_control", "discrete"),
    "double_click": ActionSpec("Double Click", "mouse_control", "discrete"),
    "play_pause": ActionSpec("Play / Pause", "media_control", "discrete"),
    "next_track": ActionSpec("Next Track", "media_control", "discrete"),
    "prev_track": ActionSpec("Previous Track", "media_control", "discrete"),
    "mute": ActionSpec("Mute", "volume_control", "discrete"),
    "volume_up": ActionSpec("Volume Up", "volume_control", "discrete"),
    "volume_down": ActionSpec("Volume Down", "volume_control", "discrete"),
    "next_slide": ActionSpec("Next Slide", "presentation", "discrete"),
    "prev_slide": ActionSpec("Previous Slide", "presentation", "discrete"),
    "toggle_pause": ActionSpec("Pause / Resume Control", "", "discrete"),
    "confirm": ActionSpec("Confirm / Unlock", "", "discrete"),
    "lock_controls": ActionSpec("Lock Controls", "", "discrete"),
    "emergency_stop": ActionSpec("Emergency Stop", "", "discrete"),
    "show_app": ActionSpec("Show TouchlessX", "", "discrete"),
    "copy": ActionSpec("Copy", "", "discrete"),
    "cut": ActionSpec("Cut", "", "discrete"),
    "paste": ActionSpec("Paste", "", "discrete"),
    "paste_previous": ActionSpec("Paste Previous Copy", "", "discrete"),
    "select_all": ActionSpec("Select All", "", "discrete"),
    "delete": ActionSpec("Delete", "", "discrete"),
    "undo": ActionSpec("Undo", "", "discrete"),
    "redo": ActionSpec("Redo", "", "discrete"),
    "page_up": ActionSpec("Page Up", "mouse_control", "discrete"),
    "page_down": ActionSpec("Page Down", "mouse_control", "discrete"),
    "scroll_top": ActionSpec("Jump to Top", "mouse_control", "discrete"),
    "scroll_bottom": ActionSpec("Jump to Bottom", "mouse_control", "discrete"),
    "next_tab": ActionSpec("Next Tab", "", "discrete"),
    "prev_tab": ActionSpec("Previous Tab", "", "discrete"),
    "new_tab": ActionSpec("New Tab", "", "discrete"),
    "close_tab": ActionSpec("Close Tab / Window", "", "discrete"),
    "switch_app": ActionSpec("Switch Application", "", "discrete"),
    "go_back": ActionSpec("Go Back", "", "discrete"),
    "go_forward": ActionSpec("Go Forward", "", "discrete"),
    "reload": ActionSpec("Reload Page", "", "discrete"),
    "zoom_in": ActionSpec("Zoom In", "", "discrete"),
    "zoom_out": ActionSpec("Zoom Out", "", "discrete"),
    "minimize_window": ActionSpec("Minimize Window", "", "discrete"),
    "restore_window": ActionSpec("Restore Window", "", "discrete"),
}

_KEY_ACTIONS = {
    "play_pause": "playpause",
    "next_track": "nexttrack",
    "prev_track": "prevtrack",
    "mute": "volumemute",
    "volume_up": "volumeup",
    "volume_down": "volumedown",
}
# Editing shortcuts; "mod" becomes Command on macOS and Ctrl elsewhere.
_EDIT_KEYS = {
    "copy": ["mod", "c"], "cut": ["mod", "x"], "paste": ["mod", "v"], "select_all": ["mod", "a"],
    "undo": ["mod", "z"], "new_tab": ["mod", "t"], "close_tab": ["mod", "w"], "reload": ["mod", "r"],
    "zoom_in": ["mod", "="], "zoom_out": ["mod", "-"],
    "next_tab": ["ctrl", "tab"], "prev_tab": ["ctrl", "shift", "tab"],  # Ctrl on every system, macOS included
}
_PLATFORM_KEYS = {  # action -> (macOS, elsewhere)
    "redo": (["command", "shift", "z"], ["ctrl", "y"]),
    "scroll_top": (["command", "up"], ["ctrl", "home"]),
    "scroll_bottom": (["command", "down"], ["ctrl", "end"]),
    "switch_app": (["command", "tab"], ["alt", "tab"]),
    "go_back": (["command", "["], ["alt", "left"]),
    "go_forward": (["command", "]"], ["alt", "right"]),
}
_PLAIN_KEYS = {"delete": "backspace", "page_up": "pageup", "page_down": "pagedown"}


def edit_keys(action: str) -> list[str]:
    """The key combination for an editing/navigation action on this operating system."""
    mac = platform.system() == "Darwin"
    if action in _PLATFORM_KEYS:
        return list(_PLATFORM_KEYS[action][0 if mac else 1])
    mod = "command" if mac else "ctrl"
    return [mod if key == "mod" else key for key in _EDIT_KEYS[action]]


_SLIDE_KEYS = {
    "arrows": {"next_slide": "right", "prev_slide": "left"},
    "page": {"next_slide": "pagedown", "prev_slide": "pageup"},
}


def action_spec(action: str) -> ActionSpec | None:
    if action.startswith(SMART_HOME_PREFIX):
        return ActionSpec(action[len(SMART_HOME_PREFIX):], "smart_home", "discrete")
    if action.startswith(SHORTCUT_PREFIX):
        return ActionSpec(action[len(SHORTCUT_PREFIX):], "", "discrete")
    if action.startswith(APP_PREFIX):
        return ActionSpec("Open " + action[len(APP_PREFIX):], "", "discrete")
    return ACTIONS.get(action)


def continuous_poses(mappings: dict[str, str]) -> set[str]:
    """The poses that, under these mappings, drive a continuous action."""
    return {g for g in FLEXIBLE_POSES if getattr(action_spec(mappings.get(g, "none")), "kind", "") == "continuous"}


def actions_for_gesture(gesture: str, smart_devices: list[str] | None = None,
                        shortcuts: list[str] | None = None, apps: list[str] | None = None) -> list[str]:
    """Action ids that make sense for a gesture. Hand poses can drive anything; a pinch,
    a motion gesture or a trained gesture is momentary, so only one-shot actions fit."""
    kinds = ("continuous", "discrete") if gesture in FLEXIBLE_POSES else ("discrete",)
    actions = ["none"]
    for kind in kinds:
        actions += [a for a, spec in ACTIONS.items() if spec.kind == kind]
    actions += [SHORTCUT_PREFIX + name for name in (shortcuts or [])]
    actions += [APP_PREFIX + name for name in (apps or [])]
    actions += [SMART_HOME_PREFIX + name for name in (smart_devices or [])]
    return actions


class ActionController:
    def __init__(
        self,
        executor: InputExecutor,
        safety: SafetyGuard,
        enabled: dict[str, bool],
        cursor_smoothing: float = 0.5,
        screen_size: tuple[int, int] | None = None,
        mappings: dict[str, str] | None = None,
        cursor_dead_zone: float = 0.0,
        active_area_margin: float = 0.0,
        scroll_speed: float = 1.0,
        drag_enabled: bool = False,
        drag_hold_seconds: float = 0.5,
        presentation_keys: str = "arrows",
        smart_home=None,
        engage_seconds: float = 0.0,
        double_click_seconds: float = 0.8,
        clock=time.monotonic,
        shortcuts: dict[str, list[str]] | None = None,
        confirm_smart_home: bool = False,
        confirm_seconds: float = 5.0,
        scroll_style: str = "follow",
    ) -> None:
        # name -> validated key list, for "shortcut:<name>" actions
        self.shortcuts = {} if shortcuts is None else shortcuts
        # Consequential actions (smart-home commands) can require a second, explicit
        # "confirm" gesture within `confirm_seconds` before they are carried out.
        self.confirm_smart_home = confirm_smart_home
        self.confirm_seconds = confirm_seconds
        self._pending: tuple[str, float] | None = None
        # Shared with the gesture engine: the poses currently mapped to continuous actions.
        self.continuous_gestures: set[str] = set(CONTINUOUS_GESTURES)
        # Per-gesture switches and confidence overrides from the gesture library.
        self.disabled_gestures: set[str] = set()
        self.gesture_confidence: dict[str, float] = {}
        # A gesture that stops everything, whatever profile is active ("none" = unset).
        self.emergency_gesture = "none"
        # Calibrated reach (x0, y0, x1, y1) in camera coordinates, or None for the margin.
        self.calibration: tuple[float, float, float, float] | None = None
        self.apps: dict[str, str] = {}  # name -> application path, for "app:<name>" actions
        self.callbacks: dict[str, object] = {}  # app-level hooks, e.g. "show_app"
        # Practice mode: gestures are recognised and reported, but nothing is sent.
        self.dry_run = False
        self._null_executor = NullExecutor()
        # How long a scroll/volume pose must be held before it starts acting, so a
        # hand passing through the pose on its way to another one changes nothing.
        self.engage_seconds = engage_seconds
        # Two click gestures within this window become a double-click.
        self.double_click_seconds = double_click_seconds
        self._clock = clock
        self._filter_x, self._filter_y = _OneEuroFilter(), _OneEuroFilter()
        self._last_cursor_time = 0.0
        self._last_left_click = -1e9
        self._last_minimize = -1e9
        self._volume_remainder = 0.0
        self.scroll_invert = False
        self.scroll_style = scroll_style
        self.scroll_vertical_only = False
        self._scroll_time = -1e9
        self._sticky_since: float | None = None
        self._momentum: dict | None = None      # the coasting scroll after a swipe
        self._flick_run: dict | None = None     # the fast movement currently in progress
        self._flick_return = (0, 0, -1e9)       # (axis, direction, until): the hand's way back
        self._reset_scroll()
        self.executor = executor
        self.safety = safety
        self.enabled = enabled
        self.mappings = dict(DEFAULT_MAPPINGS) if mappings is None else mappings
        self.cursor_smoothing = cursor_smoothing
        self.cursor_dead_zone = cursor_dead_zone
        self.active_area_margin = active_area_margin
        self.scroll_speed = scroll_speed
        self.drag_enabled = drag_enabled
        self.drag_hold_seconds = drag_hold_seconds
        self.presentation_keys = presentation_keys
        self.smart_home = smart_home
        self.screen_w, self.screen_h = screen_size or getattr(executor, "screen_size", (1920, 1080))
        self._smoothed_xy: tuple[float, float] | None = None
        self._last_norm_xy: tuple[float, float] | None = None
        self._dragging = False

    @property
    def executor(self):
        return self._null_executor if self.dry_run else self._executor

    @executor.setter
    def executor(self, value) -> None:
        self._executor = value

    def is_enabled(self, category: str) -> bool:
        return bool(self.enabled.get(category, False))

    def action_for(self, gesture: str) -> str:
        if gesture in self.disabled_gestures:
            return "none"
        action = self.mappings.get(gesture, "none")
        if action.startswith(SHORTCUT_PREFIX) and action[len(SHORTCUT_PREFIX):] not in self.shortcuts:
            return "none"
        if action.startswith(APP_PREFIX) and action[len(APP_PREFIX):] not in self.apps:
            return "none"
        return action if action_spec(action) is not None else "none"

    def _confident(self, event: GestureEvent) -> bool:
        threshold = self.gesture_confidence.get(event.name)
        if threshold is None:
            return self.safety.passes_confidence(event.confidence)
        return event.confidence >= threshold * 100

    def dispatch(self, event: GestureEvent) -> DispatchResult | None:
        """Maps a validated gesture to its configured action. Anything doubtful - low
        confidence, paused, locked, a disabled gesture - results in no action at all."""
        action = self.action_for(event.name)
        safety = self.safety
        if self._dragging and (action != "left_click" or safety.paused or safety.locked):
            self.release()
        if self._momentum is not None:
            if safety.paused or safety.locked:
                self._momentum = None
            else:
                self._coast(self._clock())  # the scroll set off by a swipe keeps rolling on its own

        confident = self._confident(event)
        held = event.name in self.continuous_gestures
        if event.name == self.emergency_gesture and event.name != "none":
            # Checked before everything else so it works in every profile and while locked.
            deliberate = event.hold_time >= 0.3 if held else event.is_trigger
            if confident and deliberate and not safety.paused:
                return self._emergency_stop()
            return None
        if action == "toggle_pause":
            # Must keep working while paused, or a gesture could pause control but never resume it.
            if not (event.is_trigger and confident):
                return None
            paused = safety.toggle_pause()
            return DispatchResult("Control Paused" if paused else "Control Resumed", "")
        if safety.paused or not confident:
            return None
        if safety.locked:
            # Locked: ordinary hand movement must do nothing. Only the unlock gesture gets through.
            if action == "confirm" and event.is_trigger:
                safety.set_locked(False)
                return DispatchResult("Controls Unlocked", "")
            return None
        if self._scroll_continues_through(event, action):
            return self._scroll(event, sticky=True)
        if not event.is_trigger:
            return self._drag(event) if action == "left_click" else None

        spec = action_spec(action)
        if spec.kind not in ("any", "continuous" if held else "discrete"):
            return None  # the engine and the mapping disagree about this pose: do nothing
        return self.run_action(action, event)

    def _emergency_stop(self) -> DispatchResult:
        self.release()
        self._pending = None
        self.safety.set_paused(True)
        return DispatchResult("Emergency Stop", "")

    def run_action(self, action: str, event: GestureEvent | None = None, force: bool = False) -> DispatchResult | None:
        """Performs one action. `force` skips the category on/off check - used by the
        UI's manual test buttons, which are a deliberate user click, not a gesture."""
        spec = action_spec(action)
        if spec is None or action == "none":
            return None
        if spec.category and not force and not self.is_enabled(spec.category):
            return None

        if spec.kind == "continuous":
            if event is None:
                return None
            if action == "safety_hold":
                return DispatchResult("Safety Hold", "")
            if action == "move_cursor":
                return self._move_cursor(event)
            if action == "scroll":
                return self._scroll(event)
            return self._adjust_volume(event)

        if action == "left_click":
            if self._click_or_double():
                return DispatchResult("Double Click", spec.category)
        elif action == "right_click":
            self.executor.click(button="right")
        elif action == "double_click":
            self.executor.double_click()
        elif action in _KEY_ACTIONS:
            self.executor.press_key(_KEY_ACTIONS[action])
        elif action in ("next_slide", "prev_slide"):
            keys = _SLIDE_KEYS.get(self.presentation_keys, _SLIDE_KEYS["arrows"])
            self.executor.press_key(keys[action])
        elif action.startswith(SHORTCUT_PREFIX):
            keys = self.shortcuts.get(spec.label)
            if not keys:
                return None
            self.executor.hotkey(parse_shortcut(keys))  # re-validated: the config file is editable
        elif action in _PLAIN_KEYS:
            self.executor.press_key(_PLAIN_KEYS[action])
        elif action in _EDIT_KEYS or action in _PLATFORM_KEYS:
            self.executor.hotkey(edit_keys(action))
        elif action == "paste_previous":
            # Needs the clipboard, which belongs to the UI thread: the app does the swap and the paste.
            callback = self.callbacks.get("paste_previous")
            if callback is None or not callback():
                return None
        elif action == "lock_controls":
            self.release()
            self._pending = None
            self.safety.set_locked(True)
            return DispatchResult("Controls Locked", "")
        elif action == "emergency_stop":
            return self._emergency_stop()
        elif action == "show_app":
            callback = self.callbacks.get("show_app")
            if callback is None:
                return None
            callback()
        elif action.startswith(APP_PREFIX):
            path = self.apps.get(action[len(APP_PREFIX):])
            if not path:
                return None
            self.executor.open_app(validate_app_path(path))  # re-validated: the config file is editable
        elif action == "minimize_window":
            # One fist, one window: a hand that wavers out of and back into the pose
            # must not take a second window with it.
            now = self._clock()
            if now - self._last_minimize < MINIMIZE_MIN_INTERVAL and not force:
                return None
            if not self.executor.minimize_window():
                return None
            self._last_minimize = now
        elif action == "restore_window":
            if not self.executor.restore_window():
                return None
        elif action == "confirm":
            pending, self._pending = self._pending, None
            if pending is None or self._clock() > pending[1]:
                return None
            return self._send_smart_home(pending[0])
        elif action.startswith(SMART_HOME_PREFIX):
            if self.confirm_smart_home and not force:
                self._pending = (action, self._clock() + self.confirm_seconds)
                return DispatchResult(f"Confirm {spec.label}?", spec.category)
            return self._send_smart_home(action)
        else:
            return None
        return DispatchResult(spec.label, spec.category)

    def _send_smart_home(self, action: str) -> DispatchResult | None:
        name = action[len(SMART_HOME_PREFIX):]
        if not self.is_enabled("smart_home") and not self.dry_run:
            return None
        if not self.dry_run and (self.smart_home is None or not self.smart_home.trigger(name)):
            return None
        return DispatchResult(name, "smart_home")

    def _click_or_double(self) -> bool:
        """Left click; a second one soon after the first completes a double-click.
        Returns True when it was the double."""
        now = self._clock()
        if now - self._last_left_click > self.double_click_seconds:
            self._last_left_click = now
            self.executor.click(button="left")
            return False
        self._last_left_click = -1e9  # a third quick click starts over
        self.executor.click_again()
        return True

    def refresh_screen(self) -> None:
        """Follows a changed screen resolution, so the cursor mapping always spans the
        whole current display. Called now and then by the UI."""
        refresh = getattr(self._executor, "refresh_screen_size", None)
        if refresh is None:
            return
        size = refresh()
        if size != (self.screen_w, self.screen_h) and min(size) > 0:
            scale_x, scale_y = size[0] / self.screen_w, size[1] / self.screen_h
            self.screen_w, self.screen_h = size
            if self._smoothed_xy is not None:  # keep the cursor at the same relative spot
                self._smoothed_xy = (self._smoothed_xy[0] * scale_x, self._smoothed_xy[1] * scale_y)

    def _scroll_continues_through(self, event: GestureEvent, action: str) -> bool:
        """True for a frame in which the scroll pose was lost only because the hand is
        moving too fast to read - scrolling should carry on rather than break off."""
        if event.name != "unknown":
            self._sticky_since = None  # a readable pose again: the allowance starts afresh next time
            return False
        now = self._clock()
        if (
            action != "none" or event.motion_delta is None
            or now - self._scroll_time > 0.4 or not self.is_enabled("mouse_control")
        ):
            return False
        if self._sticky_since is None:
            self._sticky_since = now
        # Once the allowance is used up it stays used up until the pose is seen again.
        return now - self._sticky_since <= SCROLL_STICKY_SECONDS

    def release(self) -> None:
        """Lets go of a held drag - called when the pinch ends, control is paused, or
        the app shuts down, so the mouse button is never left stuck down."""
        self._momentum = None
        if self._dragging:
            self._dragging = False
            self.executor.mouse_up()

    def _normalized_target(self, event: GestureEvent) -> tuple[float, float]:
        # The margin lets the cursor reach the screen edges without the hand having to
        # reach the edges of the camera frame, where tracking is least reliable.
        point = event.palm or event.index_tip
        if self.calibration is not None:
            # The user showed where their comfortable reach starts and ends.
            x0, y0, x1, y1 = self.calibration
            nx = (point[0] - x0) / max(0.05, x1 - x0)
            ny = (point[1] - y0) / max(0.05, y1 - y0)
        else:
            margin = max(0.0, min(0.4, self.active_area_margin))
            span = 1.0 - 2.0 * margin
            nx, ny = (point[0] - margin) / span, (point[1] - margin) / span
        return min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny))

    def _apply_cursor(self) -> None:
        x = min(self.screen_w - 1, max(0, int(round(self._smoothed_xy[0]))))
        y = min(self.screen_h - 1, max(0, int(round(self._smoothed_xy[1]))))
        self.executor.move_mouse(x, y)

    def _move_cursor(self, event: GestureEvent) -> DispatchResult | None:
        if event.palm is None and event.index_tip is None:
            return None
        nx, ny = self._normalized_target(event)
        now = self._clock()
        if now - self._last_cursor_time > 0.5:
            # The hand was away (or doing something else): start from where it is now
            # instead of gliding over from where it was last seen.
            self._filter_x.reset()
            self._filter_y.reset()
            self._last_norm_xy = None
        self._last_cursor_time = now
        if self.cursor_smoothing > 0:
            # Slider 0..0.9 -> cutoff 3 Hz (light) .. 0.3 Hz (heavy) for a resting hand.
            min_cutoff = 3.0 * 10.0 ** (-min(0.95, self.cursor_smoothing) / 0.9)
            nx = self._filter_x(nx, now, min_cutoff, 12.0)
            ny = self._filter_y(ny, now, min_cutoff, 12.0)
        if self._last_norm_xy is not None and self._smoothed_xy is not None:
            # Dead zone: ignore sub-threshold jitter so a still hand gives a still cursor.
            if max(abs(nx - self._last_norm_xy[0]), abs(ny - self._last_norm_xy[1])) < self.cursor_dead_zone:
                return None
        self._last_norm_xy = (nx, ny)
        self._smoothed_xy = (nx * self.screen_w, ny * self.screen_h)
        self._apply_cursor()
        return DispatchResult("Mouse Move", "mouse_control")

    def _drag(self, event: GestureEvent) -> DispatchResult | None:
        """Pinch-and-hold drag: after the click, a pinch held past `drag_hold_seconds`
        presses the button down and the cursor follows the hand until the pinch opens."""
        if not self.drag_enabled or not self.is_enabled("mouse_control"):
            return None
        if not self._dragging:
            if event.hold_time < self.drag_hold_seconds:
                return None
            self._dragging = True
            self.executor.mouse_down()
            return DispatchResult("Drag", "mouse_control")
        if event.motion_delta is None or self._smoothed_xy is None:
            return None
        # Relative motion, so the cursor doesn't jump to wherever the pinched fingertip is.
        span = 1.0 - 2.0 * max(0.0, min(0.4, self.active_area_margin))
        self._smoothed_xy = (
            self._smoothed_xy[0] + event.motion_delta[0] / span * self.screen_w,
            self._smoothed_xy[1] + event.motion_delta[1] / span * self.screen_h,
        )
        self._apply_cursor()
        return None

    def _reset_scroll(self) -> None:
        self._scroll_offset = [0.0, 0.0]   # hand travel since the scroll pose engaged
        self._scroll_output = [0.0, 0.0]   # the same, after the jitter filter (follow style)
        self._scroll_axis: int | None = None  # 0 = horizontal, 1 = vertical, None = not scrolling
        self._scroll_axis_time = 0.0
        self._scroll_remainder = 0.0

    def _scroll(self, event: GestureEvent, sticky: bool = False) -> DispatchResult | None:
        """Scrolls with the hand, vertically or horizontally, in one of three styles.

        "flick": a quick swipe sets the page rolling and it coasts to a stop, like on a
        phone; swipe again to keep going. Slow movement does nothing, so the hand can
        come back for the next swipe without scrolling back.

        "glide": where the hand was when the pose engaged becomes a neutral point. Hold
        the hand above it and the page keeps scrolling up, further away = faster; back
        to the middle = stop.

        "follow": the page moves with the hand like a touch screen. Simple, but bringing
        the hand back for another stroke scrolls back too.
        """
        now = self._clock()
        gap = now - self._scroll_time
        if event.motion_delta is None or (event.hold_time < self.engage_seconds and not sticky):
            self._reset_scroll()
            self._scroll_time = now
            return None
        if gap > 0.5:
            self._reset_scroll()  # the pose was away for a moment: start from a fresh neutral point
        self._scroll_time = now
        dt = min(0.1, gap) if 0 < gap <= 0.5 else 1 / 30
        if self.scroll_vertical_only:
            event = GestureEvent(event.name, event.confidence, event.index_tip, (0.0, event.motion_delta[1]),
                                 event.is_trigger, event.hold_time, event.palm)
        if self.scroll_style == "flick":
            return self._flick_scroll(event.motion_delta, dt, now)

        offset = self._scroll_offset
        offset[0] += event.motion_delta[0]
        offset[1] += event.motion_delta[1]
        if self.scroll_style == "glide":
            axis, amount = self._glide_scroll(offset, dt)
        else:
            axis, amount = self._follow_scroll(offset, now)
        if axis is None:
            return None

        # +x = right, +y = up. Hand up means a negative camera y, hence the flip.
        direction = (1.0 if axis == 0 else -1.0) * (-1.0 if self.scroll_invert else 1.0)
        self._scroll_remainder += amount * direction * self.scroll_speed
        pixels = int(self._scroll_remainder)
        if pixels == 0:
            return None
        self._scroll_remainder -= pixels
        self.executor.scroll_pixels(pixels if axis == 0 else 0, pixels if axis == 1 else 0)
        if axis == 1:
            return DispatchResult("Scroll Up" if pixels > 0 else "Scroll Down", "mouse_control")
        return DispatchResult("Scroll Right" if pixels > 0 else "Scroll Left", "mouse_control")

    def _flick_scroll(self, delta: tuple[float, float], dt: float, now: float) -> DispatchResult | None:
        """Swipe detection: a movement that is fast enough, for far enough, starts (or
        tops up) a coasting scroll in its direction."""
        vx, vy = delta[0] / dt, delta[1] / dt
        axis = 1 if abs(vy) >= abs(vx) else 0
        speed, other = (vx, vy)[axis], (vx, vy)[1 - axis]
        # Sideways scrolling has to be clearly sideways: a slanted vertical swipe is far more common.
        if abs(speed) < FLICK_RUN_SPEED or abs(speed) < (1.3 if axis == 1 else 1.8) * abs(other):
            self._flick_run = None
            return None
        sign = 1 if speed > 0 else -1
        run = self._flick_run
        if run is None or (run["axis"], run["sign"]) != (axis, sign):
            run = self._flick_run = {"axis": axis, "sign": sign, "travel": 0.0, "peak": 0.0, "fired": False}
        run["travel"] += abs(delta[axis])
        run["peak"] = max(run["peak"], abs(speed))
        if run["fired"] or run["peak"] < FLICK_SPEED or run["travel"] < FLICK_MIN_TRAVEL:
            return None
        run["fired"] = True  # one swipe, one push - however long the movement goes on

        back_axis, back_sign, until = self._flick_return
        if now < until and axis == back_axis and sign == back_sign:
            return None  # this is the hand returning from the swipe just made, not a new one
        self._flick_return = (axis, -sign, now + FLICK_RETURN_SECONDS)

        # +x = right, +y = up. Hand up means a negative camera y, hence the flip.
        direction = (sign if axis == 0 else -sign) * (-1 if self.scroll_invert else 1)
        push = min(6500.0, max(700.0, FLICK_PIXELS_PER_SPEED * run["peak"])) * self.scroll_speed
        momentum = self._momentum
        if momentum is not None and momentum["axis"] == axis and momentum["velocity"] * direction > 0:
            # Swiping again while it is still rolling adds to it.
            momentum["velocity"] = direction * min(9000.0, max(abs(momentum["velocity"]), push) + 0.4 * push)
        else:
            self._momentum = {"axis": axis, "velocity": direction * push, "time": now, "remainder": 0.0}
        if axis == 1:
            return DispatchResult("Scroll Up" if direction > 0 else "Scroll Down", "mouse_control")
        return DispatchResult("Scroll Right" if direction > 0 else "Scroll Left", "mouse_control")

    def tick(self) -> None:
        """Keeps a rolling scroll going when no camera frames are arriving to drive it
        (voice commands work with the camera off). Called by the UI's own timer."""
        if self._momentum is None:
            return
        if self.safety.paused:
            self._momentum = None
        else:
            self._coast(self._clock())

    def stop_scrolling(self) -> bool:
        rolling = self._momentum is not None
        self._momentum = None
        return rolling

    def voice_scroll(self, direction: str, speed: str = "") -> DispatchResult | None:
        """Scrolling asked for by voice. Plain "scroll down" gives one push that coasts
        to a stop (about a screenful); "slowly" or "keep" scrolls steadily until told to
        stop; "a little" / "fast" change the size of the push."""
        if not self.is_enabled("mouse_control") or direction not in ("up", "down", "left", "right"):
            return None
        axis = 1 if direction in ("up", "down") else 0
        sign = 1 if direction in ("up", "right") else -1
        steady = {"slow": 220.0, "keep": 650.0}.get(speed)
        now = self._clock()
        if steady is not None:
            self._momentum = {"axis": axis, "velocity": sign * steady * self.scroll_speed, "time": now,
                              "remainder": 0.0, "until": now + VOICE_SCROLL_MAX_SECONDS}
        else:
            push = {"small": 800.0, "fast": 5200.0}.get(speed, 2300.0) * self.scroll_speed
            self._momentum = {"axis": axis, "velocity": sign * push, "time": now, "remainder": 0.0}
        return DispatchResult("Scroll " + direction.capitalize(), "mouse_control")

    def _coast(self, now: float) -> None:
        """Advances the coasting scroll by the time passed and slows it down."""
        momentum = self._momentum
        dt = min(0.1, max(0.0, now - momentum["time"]))
        momentum["time"] = now
        if "until" in momentum:  # a steady scroll: constant speed until its time is up
            momentum["remainder"] += momentum["velocity"] * dt
            pixels = int(momentum["remainder"])
            if pixels:
                momentum["remainder"] -= pixels
                self.executor.scroll_pixels(pixels if momentum["axis"] == 0 else 0,
                                            pixels if momentum["axis"] == 1 else 0)
            if now >= momentum["until"]:
                self._momentum = None
            return
        momentum["remainder"] += momentum["velocity"] * dt
        pixels = int(momentum["remainder"])
        if pixels:
            momentum["remainder"] -= pixels
            self.executor.scroll_pixels(pixels if momentum["axis"] == 0 else 0, pixels if momentum["axis"] == 1 else 0)
        momentum["velocity"] *= math.exp(-dt / FLICK_FRICTION_SECONDS)
        if abs(momentum["velocity"]) < 60.0:
            self._momentum = None

    def scroll_guide(self, event: GestureEvent) -> tuple[float, float, float, bool] | None:
        """For the preview: (centre x, centre y, radius, scrolling?) of the glide style's
        neutral zone in camera coordinates, or None when it doesn't apply right now."""
        if (
            self.scroll_style != "glide" or event.palm is None or self.action_for(event.name) != "scroll"
            or event.hold_time < self.engage_seconds or self._clock() - self._scroll_time > 0.5
        ):
            return None
        return (event.palm[0] - self._scroll_offset[0], event.palm[1] - self._scroll_offset[1],
                GLIDE_DEAD_ZONE, self._scroll_axis is not None)

    def _glide_scroll(self, offset: list[float], dt: float) -> tuple[int | None, float]:
        """Rate control: distance from the neutral point sets the scrolling speed."""
        dead = GLIDE_DEAD_ZONE
        if self._scroll_axis is None:
            # Leave the dead zone to start; the direction taken then is kept (no sideways
            # wandering) until the hand comes back to the middle.
            if max(abs(offset[0]), abs(offset[1])) <= dead:
                return None, 0.0
            self._scroll_axis = 0 if abs(offset[0]) > abs(offset[1]) else 1
            self._scroll_remainder = 0.0
        distance = offset[self._scroll_axis]
        if abs(distance) <= dead * 0.6:  # a little hysteresis so it doesn't chatter at the edge
            self._scroll_axis = None
            return None, 0.0
        beyond = max(0.0, abs(distance) - dead)
        speed = GLIDE_PIXELS_PER_SECOND * beyond ** 1.4  # gentle near the middle, fast far out
        return self._scroll_axis, math.copysign(speed * dt, distance)

    def _follow_scroll(self, offset: list[float], now: float) -> tuple[int | None, float]:
        """Position control with a backlash filter: tracking jitter smaller than the
        slack moves nothing, while real movement passes through without any lag."""
        steps = [0.0, 0.0]
        for i in (0, 1):
            ahead = offset[i] - self._scroll_output[i]
            if abs(ahead) > FOLLOW_SLACK:
                steps[i] = ahead - math.copysign(FOLLOW_SLACK, ahead)
                self._scroll_output[i] += steps[i]
        if steps == [0.0, 0.0]:
            return None, 0.0
        dominant = 0 if abs(steps[0]) > abs(steps[1]) else 1
        if self._scroll_axis is None or now - self._scroll_axis_time > 0.35:
            if self._scroll_axis != dominant:
                self._scroll_remainder = 0.0
            self._scroll_axis = dominant  # idle long enough: free to pick a new direction
        elif dominant != self._scroll_axis and abs(steps[dominant]) > 3 * abs(steps[self._scroll_axis]) + 0.01:
            self._scroll_axis = dominant  # an unmistakable change of direction
            self._scroll_remainder = 0.0
        step = steps[self._scroll_axis]
        if step == 0.0:
            return None, 0.0
        self._scroll_axis_time = now
        return self._scroll_axis, step * FOLLOW_PIXELS_PER_FRAME_HEIGHT

    def _adjust_volume(self, event: GestureEvent) -> DispatchResult | None:
        if event.motion_delta is None or event.hold_time < self.engage_seconds:
            self._volume_remainder = 0.0
            return None
        # One volume step per 3% of the frame height travelled - hand up is louder.
        self._volume_remainder += -event.motion_delta[1] / 0.03
        steps = int(self._volume_remainder)
        if steps == 0:
            return None
        self._volume_remainder -= steps
        key = "volumeup" if steps > 0 else "volumedown"
        for _ in range(min(abs(steps), 4)):
            self.executor.press_key(key)
        return DispatchResult("Volume Up" if steps > 0 else "Volume Down", "volume_control")

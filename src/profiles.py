"""Profiles: named bundles of gesture mappings, sensitivity and feature switches.

A profile holds only what differs between ways of working (the keys in
`PROFILE_KEYS`); camera, theme and privacy settings stay global. Anything read from a
file - the store itself or a profile a user imports - goes through `validate_profile`,
which keeps only known gestures, known actions and in-range numbers.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .action_controller import ACTIONS, APP_PREFIX, SHORTCUT_PREFIX, SMART_HOME_PREFIX
from .config import DEFAULT_CONFIG
from .gesture_engine import BUILTIN_GESTURES, CUSTOM_PREFIX, FLEXIBLE_POSES

PROFILE_KEYS = ("mappings", "sensitivity", "enabled", "drag_enabled", "presentation_keys")
FORMAT_TAG = "touchlessx_profile"
MAX_NAME_LENGTH = 30

# (min, max) per sensitivity setting; imported values are clamped into these.
SENSITIVITY_RANGES = {
    "pinch_threshold": (0.2, 0.7),
    "swipe_min_distance": (0.08, 0.4),
    "swipe_max_duration": (0.2, 1.2),
    "stable_frames": (1, 6),
    "pose_hold_seconds": (0.0, 0.8),
    "engage_seconds": (0.0, 1.0),
    "double_click_seconds": (0.3, 1.0),
    "cooldown_seconds": (0.2, 2.0),
    "confidence_threshold": (0.3, 0.95),
    "cursor_smoothing": (0.0, 0.9),
    "cursor_dead_zone": (0.0, 0.02),
    "active_area_margin": (0.0, 0.3),
    "scroll_speed": (0.25, 3.0),
    "drag_hold_seconds": (0.3, 1.5),
}


class ProfileError(ValueError):
    """Raised for an invalid profile name or an unreadable profile file."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_profile(data, shortcuts=(), smart_devices=(), apps=()) -> dict:
    """Returns a clean profile built from `data`, starting from the defaults.

    Unknown gestures and settings are dropped, unknown actions become "none", numbers
    are clamped. Shortcut, application and smart-home actions survive only if that
    shortcut/application/device exists on *this* computer, so an imported file can never
    introduce key presses, programs or web requests the user didn't set up themselves.
    """
    if not isinstance(data, dict):
        raise ProfileError("This file is not a TouchlessX profile.")
    profile = {key: copy.deepcopy(DEFAULT_CONFIG[key]) for key in PROFILE_KEYS}

    mappings = data.get("mappings")
    for gesture, action in mappings.items() if isinstance(mappings, dict) else []:
        if not isinstance(gesture, str) or not isinstance(action, str):
            continue
        if gesture not in BUILTIN_GESTURES and not gesture.startswith(CUSTOM_PREFIX):
            continue
        if action.startswith(SHORTCUT_PREFIX):
            known = action[len(SHORTCUT_PREFIX):] in shortcuts
        elif action.startswith(APP_PREFIX):
            known = action[len(APP_PREFIX):] in apps
        elif action.startswith(SMART_HOME_PREFIX):
            known = action[len(SMART_HOME_PREFIX):] in smart_devices
        else:
            known = action in ACTIONS
        # Only a hand pose can be held, so only poses may carry a continuous action.
        continuous = action in ACTIONS and ACTIONS[action].kind == "continuous"
        kind_ok = known and (not continuous or gesture in FLEXIBLE_POSES)
        profile["mappings"][gesture] = action if kind_ok else "none"

    sensitivity = data.get("sensitivity")
    for key, value in sensitivity.items() if isinstance(sensitivity, dict) else []:
        if key in SENSITIVITY_RANGES and _is_number(value):
            low, high = SENSITIVITY_RANGES[key]
            clamped = min(high, max(low, value))
            profile["sensitivity"][key] = int(clamped) if key == "stable_frames" else float(clamped)

    enabled = data.get("enabled")
    for key, value in enabled.items() if isinstance(enabled, dict) else []:
        if key in profile["enabled"] and isinstance(value, bool):
            profile["enabled"][key] = value

    if isinstance(data.get("drag_enabled"), bool):
        profile["drag_enabled"] = data["drag_enabled"]
    if data.get("presentation_keys") in ("arrows", "page"):
        profile["presentation_keys"] = data["presentation_keys"]
    return profile


def clean_name(name) -> str:
    name = " ".join(str(name).split())
    if not name or len(name) > MAX_NAME_LENGTH or any(ch in name for ch in '/\\:*?"<>|'):
        raise ProfileError(f"Use a name of 1-{MAX_NAME_LENGTH} characters without / \\ : * ? \" < > |")
    return name


def _preset(**changes) -> dict:
    profile = {key: copy.deepcopy(DEFAULT_CONFIG[key]) for key in PROFILE_KEYS}
    for key, value in changes.items():
        if isinstance(value, dict):
            profile[key].update(value)
        else:
            profile[key] = value
    return profile


# Built-in profiles introduced after the first release: offered once to existing users.
LATER_BUILTINS = ("JARVIS",)


def builtin_profiles() -> dict[str, dict]:
    return {
        "Default": _preset(),
        "Work": _preset(
            # Cursor work without surprises: no media keys, no slide swipes, drag on.
            enabled={"media_control": False, "presentation": False},
            mappings={"fist": "none", "swipe_left": "none", "swipe_right": "none"},
            drag_enabled=True,
        ),
        "Presentation": _preset(
            # Hands near the screen while talking: only deliberate slide swipes act.
            enabled={"mouse_control": False, "media_control": False, "volume_control": False},
            mappings={"open_palm": "none", "pinch": "none", "two_fingers": "none", "three_fingers": "none",
                      "point": "none", "fist": "none"},
        ),
        "Air Mouse": _preset(
            # One hand as a complete mouse. The open hand (or a pointing finger) steers,
            # pinch is the left button - once to click, twice to double-click, held to
            # drag and drop - two fingers right-click, three fingers scroll, and a fist
            # is the rest pose that does nothing. No media or slide gestures to misfire.
            enabled={"media_control": False, "presentation": False, "volume_control": False},
            mappings={"open_palm": "move_cursor", "point": "move_cursor", "pinch": "left_click",
                      "two_fingers": "right_click", "three_fingers": "scroll", "fist": "none",
                      "swipe_left": "none", "swipe_right": "none"},
            drag_enabled=True,
        ),
        "Browse": _preset(
            # Reading and browsing: the open hand scrolls in both directions, a pointing
            # finger steers the cursor, a fist minimises the window, opening it restores it.
            # Text work: drag with a held pinch to select, three fingers copy, thumbs-up
            # pastes, a finger pointing left deletes and one pointing right right-clicks.
            enabled={"presentation": False, "volume_control": False},
            mappings={"open_palm": "scroll", "two_fingers": "scroll", "point": "move_cursor",
                      "three_fingers": "copy", "thumbs_up": "paste", "point_left": "delete",
                      "point_right": "right_click", "fist": "minimize_window", "hand_open": "restore_window",
                      "swipe_left": "none", "swipe_right": "none"},
            drag_enabled=True,
        ),
        "GestureAI": _preset(
            # The layout from the GestureAI specification: the open palm is a neutral
            # "safe" pose, a pointing finger steers, pinch clicks and drags, two fingers
            # scroll, a fist locks everything and a thumbs-up unlocks again.
            enabled={"volume_control": False},
            mappings={"open_palm": "safety_hold", "point": "move_cursor", "pinch": "left_click",
                      "two_fingers": "scroll", "three_fingers": "show_app", "fist": "lock_controls",
                      "thumbs_up": "confirm", "point_left": "prev_slide", "point_right": "next_slide",
                      "swipe_left": "none", "swipe_right": "none"},
            drag_enabled=True,
        ),
        "JARVIS": _preset(
            # The Iron Man layout: point to steer, pinch to click / grab and drag, open
            # palm to rest the cursor, flick to change desktop or scroll, spread thumb and
            # index (an L) to zoom, three fingers held to move the front window. A fist is
            # an emergency stop; both palms resume.
            enabled={"smart_home": False},
            mappings={"point": "move_cursor", "pinch": "left_click", "open_palm": "safety_hold",
                      "swipe_left": "prev_workspace", "swipe_right": "next_workspace",
                      "swipe_up": "scroll_up", "swipe_down": "scroll_down", "thumb_index": "zoom",
                      "two_fingers": "right_click", "fist": "emergency_stop", "three_fingers": "move_window",
                      "both_palms": "toggle_pause", "two_hand_pinch": "zoom", "thumbs_up": "confirm",
                      "hand_open": "none", "point_left": "none", "point_right": "none"},
            drag_enabled=True,
        ),
        "Media": _preset(
            # Lean-back control: play/pause, volume and track skipping; no cursor.
            enabled={"mouse_control": False, "presentation": False},
            mappings={"open_palm": "none", "pinch": "none", "two_fingers": "mute", "three_fingers": "none",
                      "swipe_left": "prev_track", "swipe_right": "next_track"},
        ),
    }


class ProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self.profiles: dict[str, dict] = {}
        self._load()
        if not self.profiles:
            self.profiles = builtin_profiles()
            self.save()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for name, profile in data.items() if isinstance(data, dict) else []:
            try:
                # Local shortcut/device names aren't known here; the action controller
                # already ignores mappings to ones that no longer exist.
                self.profiles[clean_name(name)] = validate_profile(
                    profile, shortcuts=_AnyName(), smart_devices=_AnyName(), apps=_AnyName())
            except ProfileError:
                continue

    def save(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=2)
        except OSError:
            pass

    def names(self) -> list[str]:
        return list(self.profiles)

    def get(self, name: str) -> dict:
        return copy.deepcopy(self.profiles[name])

    def set(self, name: str, profile: dict) -> None:
        self.profiles[name] = {key: copy.deepcopy(profile[key]) for key in PROFILE_KEYS}
        self.save()

    def unique_name(self, wanted: str) -> str:
        wanted = clean_name(wanted)
        name, counter = wanted, 2
        while name in self.profiles:
            suffix = f" {counter}"
            name = wanted[: MAX_NAME_LENGTH - len(suffix)] + suffix
            counter += 1
        return name

    def create(self, name: str, profile: dict) -> str:
        name = clean_name(name)
        if name in self.profiles:
            raise ProfileError(f"A profile named '{name}' already exists.")
        self.set(name, profile)
        return name

    def duplicate(self, name: str) -> str:
        copy_name = self.unique_name(f"{name} copy"[:MAX_NAME_LENGTH])
        self.set(copy_name, self.profiles[name])
        return copy_name

    def rename(self, old: str, new: str) -> str:
        new = clean_name(new)
        if new == old:
            return old
        if new in self.profiles:
            raise ProfileError(f"A profile named '{new}' already exists.")
        # Rebuilt rather than popped, so the profile keeps its place in the list.
        self.profiles = {(new if name == old else name): data for name, data in self.profiles.items()}
        self.save()
        return new

    def delete(self, name: str) -> None:
        if len(self.profiles) <= 1:
            raise ProfileError("At least one profile has to remain.")
        self.profiles.pop(name, None)
        self.save()

    def export_to(self, name: str, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({FORMAT_TAG: 1, "name": name, **self.profiles[name]}, f, indent=2)

    def import_from(self, path: Path, shortcuts=(), smart_devices=(), apps=()) -> str:
        try:
            if Path(path).stat().st_size > 1_000_000:
                raise ProfileError("That file is too large to be a profile.")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise ProfileError(f"Could not read the file: {exc}") from exc
        if not isinstance(data, dict) or FORMAT_TAG not in data:
            raise ProfileError("This file is not a TouchlessX profile.")
        profile = validate_profile(data, shortcuts, smart_devices, apps)
        try:
            name = self.unique_name(data.get("name") or Path(path).stem)
        except ProfileError:
            name = self.unique_name("Imported profile")
        self.set(name, profile)
        return name


class _AnyName:
    def __contains__(self, _name) -> bool:
        return True

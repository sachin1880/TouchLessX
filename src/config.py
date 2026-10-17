"""Configuration: defaults plus tolerant loading/saving of config/settings.json.

Loading always merges the file over `DEFAULT_CONFIG`, so a settings file written by an
older version (or a missing/corrupt one) still yields every key the app expects.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULT_MAPPINGS = {
    "open_palm": "move_cursor",
    "point": "volume",
    "three_fingers": "scroll",
    "pinch": "left_click",
    "two_fingers": "right_click",
    "fist": "play_pause",
    "swipe_left": "prev_slide",
    "swipe_right": "next_slide",
    "thumbs_up": "confirm",
    "swipe_up": "none",
    "swipe_down": "none",
    "hand_open": "none",
    "point_left": "none",
    "point_right": "none",
    "both_palms": "toggle_pause",
    "two_hand_pinch": "zoom",
    "thumb_index": "none",
}

DEFAULT_CONFIG = {
    "camera_index": 0,
    "frame_width": 960,
    "frame_height": 540,
    "show_landmarks": True,
    "action_log_enabled": True,
    "drag_enabled": False,
    "presentation_keys": "arrows",
    "scroll_invert": False,
    "scroll_style": "flick",
    "scroll_vertical_only": False,
    "clipboard_history": True,
    "voice_enabled": False,
    "voice_wake_word": "computer",
    "assistant_enabled": True,
    "email_target": "gmail",
    "speak_replies": True,
    "user_name": "",
    "ignore_resting_hand": True,
    "preferred_hand": "any",
    "mirror_preview": True,
    "calibration": None,
    "emergency_gesture": "none",
    "apps": {},
    "gesture_settings": {},
    "menu_bar_icon": True,
    "close_to_menu_bar": False,
    "check_updates": True,
    "theme": "dark",
    "accent_color": "#2f7df6",
    "onboarded": False,
    "mini_controller": True,
    "history_enabled": True,
    "history_retention_days": 30,
    "confirm_smart_home": True,
    "active_profile": "Default",
    "shortcuts": {},
    "sensitivity": {
        "pinch_threshold": 0.4,
        "swipe_min_distance": 0.22,
        "swipe_max_duration": 0.35,
        "stable_frames": 2,
        "pose_hold_seconds": 0.25,
        "engage_seconds": 0.3,
        "double_click_seconds": 0.8,
        "cooldown_seconds": 0.6,
        "confidence_threshold": 0.6,
        "cursor_smoothing": 0.5,
        "cursor_dead_zone": 0.003,
        "active_area_margin": 0.2,
        "scroll_speed": 1.0,
        "drag_hold_seconds": 0.5,
    },
    "enabled": {
        "mouse_control": True,
        "media_control": True,
        "presentation": True,
        "volume_control": True,
        "smart_home": False,
        "ai_assistant": False,
    },
    "mappings": dict(DEFAULT_MAPPINGS),
    # Built-in profiles added after first start that have been offered once already, so a
    # deleted one doesn't come back.
    "profiles_added": [],
    # Holo Desk panel positions: panel id -> [x, y, width, height] in pixels.
    "holo_panels": {},
    "holo_notes": "",
    # Full-screen Holo mode: panel id -> [x, y] as fractions of the screen.
    "holo_full_layout": {},
    # Home CCTV (cctv_client.py): the HomeSmartCCTV API address, the account email (not the
    # password - that is never stored) and the camera gestures control. The session token
    # lives in the system credential store, not here.
    "cctv_server_url": "http://localhost:8000/api",
    "cctv_email": "",
    "cctv_device_id": "",
    # Face Control (face_control.py): hands-free mode, replaces hand gestures while on.
    "face_control": False,
    "face": {
        "gain": 3.0,                 # cursor travel per head turn
        "smoothing": 0.6,
        "wink_threshold": 0.55,      # an eye is shut below this share of its normal opening
        "long_blink_seconds": 0.8,   # both eyes shut this long = pause / resume
        "scroll_speed": 1.0,
        "pointer_mode": "relative",  # "relative" (like a mouse) or "absolute" (head points)
        # Expression -> action, and for the scored ones the score (0..1) that counts.
        "expressions": {
            "left_wink": {"action": "left_click"},
            "right_wink": {"action": "none"},
            "raise_brows": {"action": "right_click", "threshold": 0.5},
            "pucker": {"action": "double_click", "threshold": 0.6},
            "mouth_open": {"action": "scroll_mode", "threshold": 0.35},
            "smile": {"action": "minimize_window", "threshold": 0.6},
        },
    },
}


def _merge(defaults: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_config() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return _merge(DEFAULT_CONFIG, data)


def save_config(path: Path, config: dict) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
    except OSError:
        return False  # Non-critical - settings just won't persist across restarts.
    return True

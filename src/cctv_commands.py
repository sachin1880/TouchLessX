"""The camera commands TouchlessX can send to HomeSmartCCTV, and which need a
confirmation gesture. No networking here, so the gesture pipeline can import it.

This is a fixed allowlist mirroring the backend's `CommandType`: a gesture, a voice
phrase or a profile file can only pick one of these names - never send anything else.
"""
from __future__ import annotations

from dataclasses import dataclass

CCTV_PREFIX = "cctv:"


@dataclass(frozen=True)
class CctvCommand:
    api_type: str            # the backend's CommandType value
    label: str
    needs_confirmation: bool  # sensitive: a thumbs-up (or "confirm") must follow


CCTV_COMMANDS: dict[str, CctvCommand] = {
    "refresh_status": CctvCommand("REFRESH_STATUS", "Camera Status", needs_confirmation=False),
    "switch_camera": CctvCommand("SWITCH_CAMERA", "Flip Camera", needs_confirmation=True),
    "capture_snapshot": CctvCommand("CAPTURE_SNAPSHOT", "Take Snapshot", needs_confirmation=True),
}


def cctv_command(action: str) -> CctvCommand | None:
    """The command behind a "cctv:<name>" action, or None if it isn't one."""
    if not action.startswith(CCTV_PREFIX):
        return None
    return CCTV_COMMANDS.get(action[len(CCTV_PREFIX):])


def cctv_actions() -> list[str]:
    return [CCTV_PREFIX + name for name in CCTV_COMMANDS]

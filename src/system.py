"""Fitting into the operating system: where user data lives, starting at login,
checking for a newer release, and the macOS menu-bar icon."""
from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "TouchlessX"
REPOSITORY = "sachin1880/TouchLessX"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
_LABEL = "com.touchlessx.app"


def is_packaged() -> bool:
    """True when running as a built application rather than from the source code."""
    return bool(getattr(sys, "frozen", False))


def data_root(source_root: Path) -> Path:
    """Where settings, logs, history and downloaded models are kept.

    From source that is the project folder. A packaged application must not write into
    itself (it may be read-only, and is replaced on every update), so it uses the
    per-user application-data folder of the operating system instead.
    """
    if not is_packaged():
        return source_root
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME


# ------------------------------------------------------------------ start at login
def launch_command(source_app: Path) -> list[str]:
    """The command that starts this installation of the app."""
    if not is_packaged():
        return [sys.executable, str(source_app)]
    executable = Path(sys.executable)
    bundle = next((p for p in executable.parents if p.suffix == ".app"), None)
    if platform.system() == "Darwin" and bundle is not None:
        # Through LaunchServices, so camera / microphone permission belongs to the app itself.
        return ["/usr/bin/open", "-a", str(bundle)]
    return [str(executable)]


class LoginItem:
    """Starts the app when the user logs in - a per-user entry, no administrator rights.

    macOS: a LaunchAgent file. Windows: a value under the user's "Run" registry key.
    Linux: an autostart .desktop file.
    """

    def __init__(self, home: Path | None = None, system: str | None = None) -> None:
        self.home = home or Path.home()
        self.system = system or platform.system()

    @property
    def path(self) -> Path | None:
        if self.system == "Darwin":
            return self.home / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
        if self.system == "Linux":
            return self.home / ".config" / "autostart" / "touchlessx.desktop"
        return None  # Windows keeps it in the registry

    def is_enabled(self) -> bool:
        if self.system == "Windows":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                    winreg.QueryValueEx(key, APP_NAME)
                return True
            except OSError:
                return False
        return self.path is not None and self.path.exists()

    def enable(self, command: list[str]) -> None:
        if self.system == "Windows":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.system == "Darwin":
            with open(self.path, "wb") as f:
                plistlib.dump({"Label": _LABEL, "ProgramArguments": command, "RunAtLoad": True,
                               "ProcessType": "Interactive"}, f)
        else:
            quoted = " ".join('"' + part.replace('"', '\\"') + '"' for part in command)
            self.path.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={quoted}\n", encoding="utf-8")

    def disable(self) -> None:
        if self.system == "Windows":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
            return
        if self.path is not None:
            self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------- update check
def parse_version(text) -> tuple[int, ...]:
    """'v1.4.0', '1.4', 'TouchlessX 2.0.1-beta' -> (1, 4, 0), (1, 4), (2, 0, 1). () if there is none."""
    match = re.search(r"\d+(?:\.\d+)*", str(text))
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return bool(a) and a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check_for_update(current: str, opener=urllib.request.urlopen, timeout: float = 6.0) -> dict:
    """Asks GitHub whether a newer release has been published.

    Returns {"status": ..., "latest": ..., "url": ...} with status "update", "current",
    "none" (nothing has been released yet) or "error". The only thing sent is an
    ordinary web request for the public release information.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME})
    try:
        with opener(request, timeout=timeout) as response:
            release = json.loads(response.read().decode("utf-8"))
        latest = str(release.get("tag_name", ""))
        url = str(release.get("html_url", "")) or RELEASES_PAGE
    except urllib.error.HTTPError as exc:
        return {"status": "none" if exc.code == 404 else "error", "latest": "", "url": RELEASES_PAGE}
    except (OSError, ValueError):
        return {"status": "error", "latest": "", "url": RELEASES_PAGE}
    if not url.startswith(f"https://github.com/{REPOSITORY}/"):
        url = RELEASES_PAGE  # never follow a link that points anywhere else
    if not parse_version(latest):
        return {"status": "none", "latest": "", "url": RELEASES_PAGE}
    return {"status": "update" if is_newer(latest, current) else "current", "latest": latest, "url": url}


# ------------------------------------------------------------------ menu-bar icon
_target_class = None


def _menu_target_class():
    """The Objective-C object menu items call back into (defined once per process)."""
    global _target_class
    if _target_class is None:
        import objc
        from Foundation import NSObject

        class TouchlessXMenuTarget(NSObject):
            def initWithCallbacks_(self, callbacks):
                self = objc.super(TouchlessXMenuTarget, self).init()
                if self is not None:
                    self.callbacks = callbacks
                return self

            def run_(self, sender):
                callback = self.callbacks.get(int(sender.tag()))
                if callback is not None:
                    callback()

        _target_class = TouchlessXMenuTarget
    return _target_class


class MenuBarIcon:
    """A macOS menu-bar item with the essentials: show the window, pause, camera, quit.
    Must be created and used on the main thread (the one the window runs on)."""

    ITEMS = ("show", "pause", "camera", "voice", "quit")

    def __init__(self, callbacks: dict) -> None:
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

        self._target = _menu_target_class().alloc().initWithCallbacks_(
            {i: callbacks[name] for i, name in enumerate(self.ITEMS) if name in callbacks})
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._item.button().setTitle_("✋")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self._entries = {}
        titles = {"show": "Show TouchlessX", "pause": "Pause Control", "camera": "Stop Camera",
                  "voice": "Turn Voice On", "quit": "Quit TouchlessX"}
        for i, name in enumerate(self.ITEMS):
            if name == "quit":
                menu.addItem_(NSMenuItem.separatorItem())
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(titles[name], "run:", "")
            entry.setTarget_(self._target)
            entry.setTag_(i)
            menu.addItem_(entry)
            self._entries[name] = entry
        self._item.setMenu_(menu)

    def update(self, paused: bool, camera_running: bool, voice_on: bool) -> None:
        self._item.button().setTitle_("⏸" if paused else "✋")
        self._entries["pause"].setTitle_("Resume Control" if paused else "Pause Control")
        self._entries["camera"].setTitle_("Stop Camera" if camera_running else "Start Camera")
        self._entries["voice"].setTitle_("Turn Voice Off" if voice_on else "Turn Voice On")

    def trigger(self, name: str) -> None:
        """Runs a menu entry as if it had been chosen (used by tests)."""
        self._target.run_(self._entries[name])

    def remove(self) -> None:
        from AppKit import NSStatusBar

        NSStatusBar.systemStatusBar().removeStatusItem_(self._item)

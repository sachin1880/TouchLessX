"""Moving and resizing other applications' windows (macOS Accessibility API).

This is native window management - separate from the panels inside TouchlessX, which
the app moves itself. It works on ordinary windows of the application in front. It
cannot move full-screen windows, some system windows, or windows of apps that refuse
accessibility changes; those calls simply report failure.

TouchlessX never moves its own window this way: the calls run on the tracking thread,
and AppKit aborts a process that does window work for itself off the main thread.
"""
from __future__ import annotations

import os
import platform

MIN_WIDTH, MIN_HEIGHT = 320, 200


class WindowManager:
    """The front window of the front application, through the Accessibility API."""

    def __init__(self) -> None:
        self.available = platform.system() == "Darwin"

    def _front_window(self):
        from AppKit import NSWorkspace
        import ApplicationServices as AS

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.processIdentifier() == os.getpid():
            return None
        element = AS.AXUIElementCreateApplication(app.processIdentifier())
        for attribute in ("AXFocusedWindow", "AXMainWindow"):
            error, window = AS.AXUIElementCopyAttributeValue(element, attribute, None)
            if not error and window is not None:
                return window
        return None

    def grab(self):
        """A handle to the window in front, or None."""
        if not self.available:
            return None
        try:
            return self._front_window()
        except Exception:
            return None

    def frame(self, window) -> tuple[float, float, float, float] | None:
        """(x, y, width, height) in screen points, top-left origin."""
        import ApplicationServices as AS

        try:
            error, position = AS.AXUIElementCopyAttributeValue(window, "AXPosition", None)
            error2, size = AS.AXUIElementCopyAttributeValue(window, "AXSize", None)
            if error or error2:
                return None
            _, point = AS.AXValueGetValue(position, AS.kAXValueCGPointType, None)
            _, extent = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
            return float(point.x), float(point.y), float(extent.width), float(extent.height)
        except Exception:
            return None

    def set_frame(self, window, x: float | None = None, y: float | None = None,
                  width: float | None = None, height: float | None = None) -> bool:
        import ApplicationServices as AS
        from Quartz import CGPoint, CGSize

        try:
            ok = True
            if width is not None and height is not None:
                size = AS.AXValueCreate(AS.kAXValueCGSizeType,
                                        CGSize(max(MIN_WIDTH, width), max(MIN_HEIGHT, height)))
                ok = AS.AXUIElementSetAttributeValue(window, "AXSize", size) == 0 and ok
            if x is not None and y is not None:
                point = AS.AXValueCreate(AS.kAXValueCGPointType, CGPoint(x, y))
                ok = AS.AXUIElementSetAttributeValue(window, "AXPosition", point) == 0 and ok
            return ok
        except Exception:
            return False

    def screen_area(self) -> tuple[float, float, float, float]:
        """The usable part of the main screen (no menu bar or Dock), top-left origin."""
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        full, visible = screen.frame(), screen.visibleFrame()
        top = full.size.height - (visible.origin.y + visible.size.height)
        return float(visible.origin.x), float(top), float(visible.size.width), float(visible.size.height)

    def arrange(self, layout: str) -> bool:
        """Snaps the front window: "left", "right", "maximize" or "center"."""
        window = self.grab()
        if window is None:
            return False
        x, y, w, h = self.screen_area()
        if layout == "left":
            return self.set_frame(window, x, y, w / 2, h)
        if layout == "right":
            return self.set_frame(window, x + w / 2, y, w / 2, h)
        if layout == "maximize":
            return self.set_frame(window, x, y, w, h)
        if layout == "center":
            frame = self.frame(window)
            if frame is None:
                return False
            return self.set_frame(window, x + (w - frame[2]) / 2, y + (h - frame[3]) / 2)
        return False


class NullWindowManager:
    """Where native window management isn't available (or in tests)."""

    available = False

    def grab(self):
        return None

    def frame(self, window):
        return None

    def set_frame(self, window, x=None, y=None, width=None, height=None) -> bool:
        return False

    def screen_area(self):
        return 0.0, 0.0, 1920.0, 1080.0

    def arrange(self, layout: str) -> bool:
        return False

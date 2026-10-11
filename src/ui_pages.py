"""The sidebar pages other than the dashboard. Each page reads and changes state only
through the `App` it is given (see ui.py), so none of them touch the pipeline directly."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from tkinter import colorchooser, filedialog

import customtkinter as ctk

from .action_controller import (
    SHORTCUT_PREFIX, SMART_HOME_PREFIX, AppError, ShortcutError, action_spec, actions_for_gesture,
    parse_shortcut, shortcut_text, validate_app_path,
)
from .colors import ACCENT_PRESETS, text_on
from .custom_gestures import CustomGestureError
from .gesture_engine import CUSTOM_PREFIX
from .training import MIN_SAMPLES_TO_TRAIN, TrainingError
from .profiles import ProfileError
from .smart_home import SmartHomeError
from .system import is_packaged
from .ui_widgets import (
    ACCENT, AMBER, BG, CATEGORIES, GREEN, PANEL_ALT, RED, TEXT, TEXT_DIM, TRACK,
    button, card, entry, gesture_glyph, gesture_label, label, option_menu, slider_row, switch, switch_row,
)


def action_label(action: str) -> str:
    spec = action_spec(action)
    if spec is None:
        return "No action"
    if action.startswith(SMART_HOME_PREFIX):
        return f"Smart Home: {spec.label}"
    if action.startswith(SHORTCUT_PREFIX):
        return f"Shortcut: {spec.label}"
    return spec.label  # "Open <name>" for applications


class Page(ctk.CTkFrame):
    title = ""
    subtitle = ""

    def __init__(self, app) -> None:
        super().__init__(app, fg_color=BG)
        self.app = app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 8))
        label(head, self.title, 22, True).pack(anchor="w")
        label(head, self.subtitle, 12, color=TEXT_DIM).pack(anchor="w")
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=(12, 4), pady=(0, 12))
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def rebuild(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.build()

    def on_show(self) -> None:
        """Called every time the page becomes visible."""

    def section(self, title: str, hint: str = "") -> ctk.CTkFrame:
        frame = card(self.body)
        frame.pack(fill="x", padx=12, pady=8)
        label(frame, title, 15, True).pack(anchor="w", padx=18, pady=(14, 0))
        if hint:
            label(frame, hint, 11, color=TEXT_DIM, justify="left", wraplength=760).pack(anchor="w", padx=18)
        ctk.CTkFrame(frame, fg_color="transparent", height=6).pack()
        return frame

    @staticmethod
    def end(frame: ctk.CTkFrame) -> None:
        ctk.CTkFrame(frame, fg_color="transparent", height=8).pack()

    @staticmethod
    def note(parent, text: str, color=TEXT_DIM) -> ctk.CTkLabel:
        item = label(parent, text, 11, color=color, justify="left", wraplength=760)
        item.pack(anchor="w", padx=18, pady=(2, 0))
        return item

    def sensitivity_slider(self, parent, key: str, title: str, hint: str, low: float, high: float,
                           fmt: str = "{:.2f}") -> None:
        value = self.app.config_data["sensitivity"][key]
        slider_row(parent, title, hint, value, low, high,
                   lambda v, k=key: self.app.set_sensitivity(k, round(v, 4)), fmt)

    def option_switch(self, parent, key: str, title: str, hint: str, on_change=None) -> None:
        """A switch bound to a top-level config flag."""
        var = ctk.BooleanVar(value=bool(self.app.config_data.get(key)))
        handler = on_change or (lambda value: self.app.set_option(key, value))
        var.trace_add("write", lambda *_: handler(bool(var.get())))
        self._vars = getattr(self, "_vars", []) + [var]  # Tk drops traces of collected variables
        switch_row(parent, title, hint, var)


class GestureStudioPage(Page):
    title = "Gesture Studio"
    subtitle = "Choose what each gesture does, tune recognition, and practise safely."

    def build(self) -> None:
        app = self.app
        box = self.section(
            "Practice area",
            "Try gestures without consequences. In practice mode gestures are recognised and reported, "
            "but no click, key or webhook is sent.",
        )
        self._practice = ctk.BooleanVar(value=app.action_controller.dry_run)
        self._practice.trace_add("write", lambda *_: app.set_practice(self._practice.get()))
        switch_row(box, "Practice mode", "Switches itself off when TouchlessX restarts.", self._practice)
        live = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
        live.pack(fill="x", padx=18, pady=6)
        self._live_glyph = label(live, "–", 26, width=50)
        self._live_glyph.pack(side="left", padx=(10, 4), pady=10)
        text = ctk.CTkFrame(live, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        self._live_name = label(text, "Show a hand to the camera", 14, True)
        self._live_name.pack(anchor="w")
        self._live_detail = label(text, "", 11, color=TEXT_DIM)
        self._live_detail.pack(anchor="w")
        self._seen_label = self.note(box, "Gestures recognised since you opened this page: none yet.")
        self._seen: dict[str, int] = {}
        self._last_seen = "none"
        self.end(box)

        box = self.section(
            "Gesture → action mapping",
            "Held poses drive continuous actions (cursor, scroll, volume); every other gesture fires once. "
            "Choose 'No action' to disable a gesture. Changes are saved to the active profile.",
        )
        devices, shortcuts = app.smart_home.names(), list(app.config_data["shortcuts"])
        apps, settings = list(app.config_data["apps"]), app.config_data["gesture_settings"]
        self._kind_labels: dict[str, ctk.CTkLabel] = {}
        for gesture in app.all_gestures():
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            label(row, gesture_glyph(gesture), 20, width=34).pack(side="left")
            label(row, gesture_label(gesture), 13, True, width=150, anchor="w").pack(side="left", padx=(6, 0))
            self._kind_labels[gesture] = label(row, "", 11, color=TEXT_DIM, width=70, anchor="w")
            self._kind_labels[gesture].pack(side="left")

            enabled = ctk.BooleanVar(value=settings.get(gesture, {}).get("enabled", True))
            enabled.trace_add("write", lambda *_, g=gesture, v=enabled: app.set_gesture_setting(g, "enabled", v.get()))
            self._vars = getattr(self, "_vars", []) + [enabled]
            switch(row, enabled).pack(side="right", padx=(10, 0))
            choices = {action_label(a): a for a in actions_for_gesture(gesture, devices, shortcuts, apps)}
            current = action_label(app.config_data["mappings"].get(gesture, "none"))
            menu = option_menu(
                row, list(choices), current if current in choices else "No action",
                lambda chosen, g=gesture, c=choices: self._map(g, c[chosen]),
            )
            menu.pack(side="right")
        self._refresh_kinds()
        self.note(box, "The switch on the right turns a gesture off without losing its mapping.")
        self.end(box)

        box = self.section(
            "Per-gesture tuning",
            "Give one gesture its own confidence threshold or cooldown. Leave a slider at the far left to "
            "follow the general setting below.",
        )
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        label(row, "Gesture", 13, True).pack(side="left")
        names = {gesture_label(g): g for g in app.all_gestures()}
        chosen = getattr(self, "_tuned", None)
        if chosen not in names.values():
            chosen = app.all_gestures()[0]
        self._tuned = chosen
        option_menu(row, list(names), gesture_label(chosen), lambda name: self._tune(names[name])).pack(side="right")
        own = settings.get(chosen, {})
        slider_row(box, "Confidence threshold", "Far left = use the general minimum confidence.",
                   own.get("confidence", 0.29), 0.29, 0.99,
                   lambda v: app.set_gesture_setting(chosen, "confidence", None if v < 0.3 else round(v, 2)),
                   "{:.2f}", 70)
        slider_row(box, "Cooldown", "Far left = use the general gesture cooldown.",
                   own.get("cooldown", 0.0), 0.0, 3.0,
                   lambda v: app.set_gesture_setting(chosen, "cooldown", None if v < 0.05 else round(v, 2)),
                   "{:.2f}s", 60)
        self.end(box)

        box = self.section("Recognition sensitivity")
        self.sensitivity_slider(box, "pinch_threshold", "Pinch distance",
                                "How close thumb and index must be to count as a pinch. Higher = easier.", 0.2, 0.7)
        self.sensitivity_slider(box, "pose_hold_seconds", "Pose hold time",
                                "How long a one-shot pose (fist, two fingers, thumbs-up, custom) must be held "
                                "before it fires. Higher = fewer accidental triggers. Pinch always acts at once.",
                                0.0, 0.8, "{:.2f}s")
        self.sensitivity_slider(box, "engage_seconds", "Scroll / volume engage time",
                                "How long the scroll or volume pose must be held before it starts acting.",
                                0.0, 1.0, "{:.2f}s")
        self.sensitivity_slider(box, "swipe_min_distance", "Swipe distance",
                                "How far the hand must travel, as a fraction of the frame.", 0.08, 0.4)
        self.sensitivity_slider(box, "swipe_max_duration", "Swipe time window",
                                "The swipe must complete within this many seconds - shorter means only fast "
                                "flicks count.", 0.2, 1.2, "{:.2f}s")
        self.sensitivity_slider(box, "cooldown_seconds", "Gesture cooldown",
                                "Minimum gap before the same one-shot gesture can fire again.", 0.2, 2.0, "{:.2f}s")
        self.sensitivity_slider(box, "confidence_threshold", "Minimum confidence",
                                "Detections below this confidence are ignored.", 0.3, 0.95)
        self.end(box)

        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=8)
        button(row, "Reset profile to defaults", app.reset_settings, "ghost", 200).pack(side="left")
        self._refresh_live()

    def _map(self, gesture: str, action: str) -> None:
        self.app.set_mapping(gesture, action)
        self._refresh_kinds()

    def _tune(self, gesture: str) -> None:
        self._tuned = gesture
        self.rebuild()

    def _refresh_kinds(self) -> None:
        held = self.app.gesture_engine.continuous_gestures
        for gesture, kind in self._kind_labels.items():
            if gesture.startswith(CUSTOM_PREFIX):
                kind.configure(text="trained")
            elif self.app.config_data["mappings"].get(gesture, "none") == "none":
                kind.configure(text="unused")
            else:
                kind.configure(text="while held" if gesture in held else "one-shot")

    def _refresh_live(self) -> None:
        if not self._live_name.winfo_exists():
            return  # the page was rebuilt; the new build started its own loop
        if self.winfo_ismapped():
            app = self.app
            event = app.last_event
            if event is None or event.name == "none":
                self._live_glyph.configure(text="–")
                self._live_name.configure(
                    text="Show a hand to the camera" if app.camera_running else "The camera is off")
                self._live_detail.configure(text="")
                self._last_seen = "none"
            else:
                action = action_label(app.action_controller.action_for(event.name))
                self._live_glyph.configure(text=gesture_glyph(event.name))
                self._live_name.configure(text=f"{gesture_label(event.name)}  →  {action}")
                self._live_detail.configure(
                    text=f"held {event.hold_time:.1f}s  ·  confidence {event.confidence:.0f}%")
                if event.name != self._last_seen and event.name != "unknown":
                    self._seen[event.name] = self._seen.get(event.name, 0) + 1
                    self._seen_label.configure(text="Gestures recognised since you opened this page: " + ",  ".join(
                        f"{gesture_label(g)} ×{n}" for g, n in self._seen.items()))
                self._last_seen = event.name
        self.after(200, self._refresh_live)

    def on_show(self) -> None:
        self.rebuild()  # custom gestures, shortcuts or smart-home devices may have changed


class SystemControlsPage(Page):
    title = "System Controls"
    subtitle = "Mouse & keyboard behaviour."

    def build(self) -> None:
        app = self.app
        box = self.section("Mouse")
        switch_row(box, "Mouse control", "Cursor movement, clicks, scrolling and drag.",
                   app.toggle_vars["mouse_control"])
        self.sensitivity_slider(box, "active_area_margin", "Cursor speed",
                                "How little hand travel moves the cursor across the whole screen. Higher = "
                                "faster, and you stay further from the edges of the camera frame.", 0.0, 0.3)
        self.sensitivity_slider(box, "cursor_smoothing", "Cursor smoothing",
                                "How strongly a resting hand is steadied. Fast movements are always "
                                "followed closely.", 0.0, 0.9)
        self.sensitivity_slider(box, "cursor_dead_zone", "Dead zone",
                                "Hand movement smaller than this is treated as jitter and ignored.",
                                0.0, 0.02, "{:.3f}")
        self.sensitivity_slider(box, "double_click_seconds", "Double-click window",
                                "Two click gestures within this time become a double-click (opens files).",
                                0.3, 1.0, "{:.2f}s")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left")
        label(text, "Scroll style", 13, True).pack(anchor="w")
        label(text, "Swipe: flick your hand up or down and the page rolls on and coasts to a stop, like on a "
                    "phone;\nslow movement does nothing. Glide: hold your hand above or below where it "
                    "started - further = faster.\nFollow: the page moves with your hand, like a touch screen.",
              11, color=TEXT_DIM, justify="left").pack(anchor="w")
        styles = {"Swipe (flick and coast)": "flick", "Glide (hold to scroll)": "glide", "Follow my hand": "follow"}
        current = next((k for k, v in styles.items() if v == app.config_data["scroll_style"]),
                       "Swipe (flick and coast)")
        option_menu(row, list(styles), current,
                    lambda chosen: app.set_option("scroll_style", styles[chosen]), 190).pack(side="right")
        self.sensitivity_slider(box, "scroll_speed", "Scroll speed",
                                "Multiplier for the scroll gesture.", 0.25, 3.0, "{:.2f}x")
        self.option_switch(box, "scroll_vertical_only", "Scroll up and down only",
                           "Ignores sideways movement, so a slanted swipe can't scroll the page sideways.")
        self.option_switch(box, "scroll_invert", "Reverse scroll direction",
                           "By default the page scrolls the way your hand moves: hand up scrolls up, hand "
                           "right scrolls right. Switch on if it feels backwards.")
        self.end(box)

        box = self.section("Drag", "Optional. Enable once the basic controls feel stable.")
        self.option_switch(box, "drag_enabled", "Pinch-and-hold drag",
                           "Keep the click gesture held to press the mouse button down; move your hand to drag, "
                           "open the pinch to drop.")
        self.sensitivity_slider(box, "drag_hold_seconds", "Hold time before dragging", "", 0.3, 1.5, "{:.2f}s")
        self.end(box)

        box = self.section(
            "Keyboard shortcuts",
            "Name a key combination and it becomes an action you can map to any one-shot gesture in Gesture "
            "Studio. Only key presses from an approved list are possible - a gesture can never run a command.",
        )
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._shortcut_name = entry(row, "Name, e.g. Switch app", 200)
        self._shortcut_name.pack(side="left", padx=(0, 8))
        self._shortcut_keys = entry(row, "Keys, e.g. Cmd+Tab", 200)
        self._shortcut_keys.pack(side="left", padx=(0, 8))
        button(row, "Add", self._add_shortcut, "primary", 70).pack(side="left")
        self._shortcut_message = self.note(
            box, "Modifiers: Cmd, Ctrl, Alt, Shift, Win - plus one letter, digit, F-key, arrow or editing key.")
        for name, keys in app.config_data["shortcuts"].items():
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=4)
            label(row, name, 13, True).pack(side="left", padx=12, pady=8)
            label(row, shortcut_text(keys), 12, color=TEXT_DIM).pack(side="left")
            button(row, "Remove", lambda n=name: self._remove_shortcut(n), "danger", 80).pack(side="right", padx=8)
            button(row, "Test in 3 s", lambda n=name: self._test_shortcut(n), "ghost", 100).pack(side="right")
        self.end(box)

        box = self.section(
            "Applications",
            "Pick an application and it becomes an 'Open …' action for one-shot gestures. Only application "
            "bundles/executables can be chosen, and they are started without arguments.",
        )
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._app_name = entry(row, "Name, e.g. Calculator", 200)
        self._app_name.pack(side="left", padx=(0, 8))
        button(row, "Choose application…", self._add_app, "primary", 180).pack(side="left")
        self._app_message = self.note(box, "")
        for name, path in app.config_data["apps"].items():
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=4)
            label(row, name, 13, True).pack(side="left", padx=12, pady=8)
            label(row, path[-60:], 11, color=TEXT_DIM).pack(side="left")
            button(row, "Remove", lambda n=name: self._remove_app(n), "danger", 80).pack(side="right", padx=8)
        self.end(box)

        box = self.section(
            "Pointer calibration",
            "Optional. Show TouchlessX the corners of the area your hand comfortably covers; the cursor then "
            "spans the whole screen inside it. This replaces the Cursor speed setting until you clear it.",
        )
        calibration = app.config_data["calibration"]
        self._calibration_status = self.note(
            box, "Calibrated: your reach covers {:.0f}% × {:.0f}% of the camera frame.".format(
                (calibration[2] - calibration[0]) * 100, (calibration[3] - calibration[1]) * 100)
            if calibration else "Not calibrated - using Cursor speed.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=8)
        button(row, "Calibrate", self._calibrate, "primary", 120).pack(side="left", padx=(0, 8))
        button(row, "Clear", self._clear_calibration, "ghost", 80).pack(side="left")
        self.end(box)

        box = self.section("Emergency pause")
        self.note(box, "Space or Esc (while TouchlessX is in front), the Pause button, and the floating mini "
                       "controller's Pause button all stop gesture actions at once. You can also map a gesture "
                       "to 'Pause / Resume Control' in Gesture Studio.")
        self.end(box)

        box = self.section("Input permission")
        self._permission = label(box, "", 12, justify="left", wraplength=760)
        self._permission.pack(anchor="w", padx=18, pady=(0, 8))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18)
        button(row, "Request permission", self._request, "primary", 170).pack(side="left", padx=(0, 8))
        button(row, "Check again", self._refresh_permission, "ghost").pack(side="left")
        self.end(box)
        self._refresh_permission()

    def _add_shortcut(self) -> None:
        name = " ".join(self._shortcut_name.get().split())
        try:
            if not name or len(name) > 24 or ":" in name:
                raise ShortcutError("Give the shortcut a name of 1-24 characters (no ':').")
            if name in self.app.config_data["shortcuts"]:
                raise ShortcutError(f"A shortcut named '{name}' already exists.")
            keys = parse_shortcut(self._shortcut_keys.get())
        except ShortcutError as exc:
            self._shortcut_message.configure(text=str(exc), text_color=RED)
            return
        self.app.set_shortcut(name, keys)
        self.rebuild()

    def _remove_shortcut(self, name: str) -> None:
        self.app.set_shortcut(name, None)
        self.rebuild()

    def _test_shortcut(self, name: str) -> None:
        self._shortcut_message.configure(text=f"Sending '{name}' in 3 seconds - click into the target app…",
                                         text_color=AMBER)
        message = self._shortcut_message
        self.app.test_action(SHORTCUT_PREFIX + name, 3000, lambda ok: message.winfo_exists() and message.configure(
            text=f"Sent '{name}'." if ok else "Could not send it - input control is unavailable or practice "
                                             "mode is on.", text_color=GREEN if ok else RED))

    def _add_app(self) -> None:
        name = " ".join(self._app_name.get().split())
        if not name or len(name) > 24 or ":" in name or name in self.app.config_data["apps"]:
            self._app_message.configure(text="Give it a new name of 1-24 characters (no ':') first.", text_color=RED)
            return
        if platform.system() == "Darwin":
            path = filedialog.askopenfilename(parent=self.app, title="Choose an application",
                                              initialdir="/Applications", filetypes=[("Applications", "*.app")])
        else:
            path = filedialog.askopenfilename(parent=self.app, title="Choose an application",
                                              filetypes=[("Programs", "*.exe")])
        if not path:
            return
        try:
            self.app.set_app(name, validate_app_path(path))
        except AppError as exc:
            self._app_message.configure(text=str(exc), text_color=RED)
            return
        self.rebuild()

    def _remove_app(self, name: str) -> None:
        self.app.set_app(name, None)
        self.rebuild()

    def _calibrate(self, step: int = 0, countdown: int = 4, first=None) -> None:
        """Two captures, each after a countdown: top-left, then bottom-right of the reach."""
        status = self._calibration_status
        if not status.winfo_exists():
            return
        corner = ("TOP-LEFT", "BOTTOM-RIGHT")[step]
        if countdown > 0:
            status.configure(text=f"Hold your open hand at the {corner} of your comfortable area…  {countdown}",
                             text_color=AMBER)
            self.after(1000, lambda: self._calibrate(step, countdown - 1, first))
            return
        event = self.app.last_event
        if event is None or event.palm is None:
            status.configure(text="No hand was visible - calibration cancelled.", text_color=RED)
            return
        if step == 0:
            self._calibrate(1, 4, event.palm)
            return
        x0, y0, x1, y1 = first[0], first[1], event.palm[0], event.palm[1]
        if x1 - x0 < 0.15 or y1 - y0 < 0.15:
            status.configure(text="Those two positions are too close together (or swapped). Calibration "
                                  "cancelled - nothing was changed.", text_color=RED)
            return
        self.app.set_option("calibration", [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)])
        status.configure(text="Calibrated: your reach covers {:.0f}% × {:.0f}% of the camera frame.".format(
            (x1 - x0) * 100, (y1 - y0) * 100), text_color=GREEN)

    def _clear_calibration(self) -> None:
        self.app.set_option("calibration", None)
        self._calibration_status.configure(text="Not calibrated - using Cursor speed.", text_color=TEXT_DIM)

    def _request(self) -> None:
        self.app.executor.request_permission()
        self.after(1500, self._refresh_permission)

    def _refresh_permission(self) -> None:
        if self.app.input_ok():
            self._permission.configure(text="● Ready. TouchlessX can move the mouse and send keys.",
                                       text_color=GREEN)
        elif platform.system() == "Darwin":
            self._permission.configure(
                text="● Not permitted. Open System Settings → Privacy & Security → Accessibility and enable "
                     "the app you launched TouchlessX from (Terminal, VS Code, …), then restart TouchlessX. "
                     "Until then gestures are detected but nothing is controlled.",
                text_color=AMBER)
        else:
            self._permission.configure(
                text="● Input control could not be initialised, so TouchlessX is in preview-only mode. "
                     "On Linux, mouse control needs an X11 session.", text_color=AMBER)

    def on_show(self) -> None:
        self._refresh_permission()


class MediaPage(Page):
    title = "Media & Presentation"
    subtitle = "Music, video and slides."

    def build(self) -> None:
        app = self.app
        box = self.section("Media")
        switch_row(box, "Media control", "Play / pause and track skipping.", app.toggle_vars["media_control"])
        switch_row(box, "Volume control", "Volume up / down and mute.", app.toggle_vars["volume_control"])
        self._buttons(box, [("⏮  Previous", "prev_track"), ("⏯  Play / Pause", "play_pause"),
                            ("⏭  Next", "next_track"), ("Vol −", "volume_down"), ("Vol +", "volume_up"),
                            ("Mute", "mute")])
        self.end(box)

        box = self.section("Presentation")
        switch_row(box, "Presentation control", "Swipe to move between slides.", app.toggle_vars["presentation"])
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        label(row, "Slide keys", 13, True).pack(side="left")
        keys = {"Arrow keys  ← →": "arrows", "Page Up / Page Down": "page"}
        current = next((k for k, v in keys.items() if v == app.config_data.get("presentation_keys")),
                       "Arrow keys  ← →")
        option_menu(row, list(keys), current,
                    lambda chosen: app.set_option("presentation_keys", keys[chosen])).pack(side="right")
        self._buttons(box, [("◀  Previous slide", "prev_slide"), ("Next slide  ▶", "next_slide")], delay_ms=3000)
        self.note(box, "Slide test buttons fire after 3 seconds, so you have time to click into your slideshow.")
        self.end(box)

        self._status = label(self.body, "Use the buttons to check that this computer accepts the shortcuts.",
                             12, color=TEXT_DIM)
        self._status.pack(anchor="w", padx=14, pady=6)

        box = self.section("Compatibility notes")
        for text in (
            "Media keys act on whichever app currently owns media playback (Music, Spotify, a browser tab, …) - "
            "the same as the keys on your keyboard. If nothing is playing, the OS decides which app starts.",
            "Slide keys go to the app in front. Keynote, PowerPoint, Google Slides and PDF viewers accept arrow "
            "keys in slideshow mode; some viewers only react to Page Up / Page Down.",
            "Tested on macOS only so far. Windows and Linux use the same actions but are unverified.",
        ):
            self.note(box, "•  " + text)
        self.end(box)

    def _buttons(self, parent, items: list[tuple[str, str]], delay_ms: int = 0) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=8)
        for text, action in items:
            button(row, text, lambda a=action: self._test(a, delay_ms), "ghost", 130).pack(side="left", padx=(0, 8))

    def _test(self, action: str, delay_ms: int) -> None:
        name = action_label(action)
        if delay_ms:
            self._status.configure(text=f"Sending '{name}' in {delay_ms // 1000} seconds…", text_color=AMBER)
        status = self._status
        self.app.test_action(action, delay_ms, lambda ok: status.winfo_exists() and status.configure(
            text=f"Sent '{name}'." if ok else f"Could not send '{name}' - input control is unavailable or "
                                             "practice mode is on.",
            text_color=GREEN if ok else RED))


class SmartHomePage(Page):
    title = "Smart Home"
    subtitle = "Trigger compatible devices through your hub's webhooks."

    def build(self) -> None:
        app = self.app
        box = self.section(
            "Integration",
            "TouchlessX never drives hardware directly. Add a webhook from your hub (Home Assistant, "
            "Node-RED, IFTTT, …); it then appears as an action you can map to any one-shot gesture in "
            "Gesture Studio. Each trigger sends an HTTP POST with a small JSON body.",
        )
        switch_row(box, "Smart Home control", "Off by default. Gestures only reach devices while this is on.",
                   app.toggle_vars["smart_home"])
        self.option_switch(box, "confirm_smart_home", "Ask for confirmation",
                           "A device gesture only arms the command; it is sent when you then show the gesture "
                           "mapped to 'Confirm' (thumbs-up by default) within 5 seconds.")
        self.end(box)

        box = self.section("Add a device")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._name = entry(row, "Name, e.g. Desk lamp", 180)
        self._name.pack(side="left", padx=(0, 8))
        self._url = entry(row, "https://hub.local/api/webhook/…", 330)
        self._url.pack(side="left", padx=(0, 8))
        self._token = entry(row, "Bearer token (optional)", 190, show="•")
        self._token.pack(side="left", padx=(0, 8))
        button(row, "Add", self._add, "primary", 70).pack(side="left")
        self._message = self.note(box, "Tokens are stored locally in config/smart_home.json (not uploaded, "
                                       "not committed to git, never included in exported profiles).")
        self.end(box)

        box = self.section("Devices")
        if not app.smart_home.devices:
            self.note(box, "No devices yet.")
        self._status_labels: dict[str, ctk.CTkLabel] = {}
        for device in app.smart_home.devices:
            name = device["name"]
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=4)
            label(row, "⌂", 18, color=AMBER, width=30).pack(side="left", padx=(10, 0), pady=8)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=8)
            label(text, name, 13, True).pack(anchor="w")
            label(text, device["url"][:70], 11, color=TEXT_DIM).pack(anchor="w")
            button(row, "Remove", lambda n=name: self._remove(n), "danger", 80).pack(side="right", padx=8)
            button(row, "Test", lambda n=name: self._test(n), "ghost", 70).pack(side="right")
            status = label(row, app.smart_home.last_status.get(name, ""), 11, color=TEXT_DIM)
            status.pack(side="right", padx=10)
            self._status_labels[name] = status
        self.end(box)

    def _add(self) -> None:
        try:
            self.app.smart_home.add_device(self._name.get(), self._url.get(), self._token.get())
        except SmartHomeError as exc:
            self._message.configure(text=str(exc), text_color=RED)
            return
        self.rebuild()

    def _remove(self, name: str) -> None:
        self.app.smart_home.remove_device(name)
        self.app.unmap_action(SMART_HOME_PREFIX + name)
        self.rebuild()

    def _test(self, name: str) -> None:
        self.app.smart_home.trigger(name)
        self._poll_status(name, 12)

    def _poll_status(self, name: str, tries: int) -> None:
        status_label = self._status_labels.get(name)
        if status_label is None or not status_label.winfo_exists():
            return
        status = self.app.smart_home.last_status.get(name, "")
        status_label.configure(text=status[:60], text_color=GREEN if status.startswith("OK") else (
            RED if status.startswith("Failed") else TEXT_DIM))
        if status.startswith("Sending") and tries > 0:
            self.after(500, lambda: self._poll_status(name, tries - 1))


class InsightsPage(Page):
    title = "AI Insights"
    subtitle = "Usage, recognition quality and suggestions. Everything here stays on this computer."

    def build(self) -> None:
        app = self.app
        history, now = app.history, time.time()
        week = now - 7 * 86400

        box = self.section("Usage", "Actions performed by gestures, from the local history.")
        tiles = ctk.CTkFrame(box, fg_color="transparent")
        tiles.pack(fill="x", padx=14)
        day_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        for title, value in (("Today", history.total(day_start)), ("Last 7 days", history.total(week)),
                             ("All time", history.total())):
            tile = ctk.CTkFrame(tiles, fg_color=PANEL_ALT, corner_radius=10)
            tile.pack(side="left", fill="x", expand=True, padx=4)
            label(tile, title, 11, color=TEXT_DIM).pack(anchor="w", padx=12, pady=(8, 0))
            label(tile, str(value), 20, True).pack(anchor="w", padx=12, pady=(0, 8))

        columns = ctk.CTkFrame(box, fg_color="transparent")
        columns.pack(fill="x", padx=18, pady=(10, 0))
        columns.grid_columnconfigure((0, 1), weight=1, uniform="usage")
        self._bars(columns, 0, "Most used actions (7 days)", history.top("action", week))
        self._bars(columns, 1, "Actions per day", history.daily(7))
        if not app.config_data["history_enabled"]:
            self.note(box, "History is switched off in Settings, so nothing new is being recorded.", AMBER)
        self.end(box)

        box = self.section("Recognition quality", "From this session and the last 7 days of history.")
        stats = app.stats
        lines = []
        if stats.hand_frames:
            lines.append(f"Recognised poses: {stats.accuracy:.0f}% of the frames with a hand in view "
                         f"(the rest matched no gesture).")
            lines.append(f"Average tracking confidence: {stats.avg_confidence:.0f}%  ·  "
                         f"processing {stats.avg_latency_ms:.0f} ms per frame at {stats.fps:.0f} FPS.")
        else:
            lines.append("No hand has been tracked in this session yet.")
        if history.total(week):
            lines.append(f"{history.low_confidence_share(week):.0f}% of the last week's actions were taken on a "
                         "detection below 75% confidence.")
        brief = [(g, n) for g, n in app.gesture_engine.brief_poses.most_common(4) if g != "pinch"]
        if brief:
            lines.append("Shown too briefly to fire this session: " + ",  ".join(
                f"{gesture_label(g)} ×{n}" for g, n in brief) + ".")
        for line in lines:
            self.note(box, "•  " + line)
        self.end(box)

        box = self.section("Suggestions", "Rule-based tips from the figures above - nothing is sent anywhere.")
        for tip in app.suggestions():
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=3)
            label(row, "✦", 14, color=AMBER, width=28).pack(side="left", padx=(8, 0), pady=8)
            label(row, tip, 12, justify="left", wraplength=720).pack(side="left", padx=6, pady=8)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=(8, 0))
        button(row, "Refresh", self.rebuild, "ghost", 100).pack(side="left", padx=(0, 8))
        button(row, "Clear history", self._clear_history, "danger", 130).pack(side="left")
        self.end(box)

        box = self.section("Model training history", "Custom-gesture models trained in the Training Studio.")
        versions = app.models.versions
        if not versions:
            self.note(box, "No model has been trained yet.")
        for info in reversed(versions):
            accuracy = "n/a" if info["accuracy"] is None else f"{info['accuracy'] * 100:.0f}%"
            active = "   ● active" if info["version"] == app.models.active else ""
            self.note(box, f"•  v{info['version']}  ·  {info['created']}  ·  {', '.join(info['labels'])}  ·  "
                           f"{info['samples']} samples  ·  validation accuracy {accuracy}{active}")
        button(box, "Open Training Studio", lambda: app.show_page("Training Studio"), "ghost", 190).pack(
            anchor="w", padx=18, pady=8)
        self.end(box)

    def _bars(self, parent, column: int, title: str, rows: list[tuple[str, int]]) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=column, sticky="new", padx=(0, 12))
        label(frame, title, 12, True).pack(anchor="w", pady=(0, 4))
        peak = max([count for _, count in rows], default=0)
        if peak == 0:
            label(frame, "Nothing recorded yet.", 11, color=TEXT_DIM).pack(anchor="w")
            return
        for name, count in rows:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            label(row, name, 11, width=120, anchor="w", height=18).pack(side="left")
            label(row, str(count), 11, color=TEXT_DIM, width=40, anchor="e", height=18).pack(side="right")
            bar = ctk.CTkProgressBar(row, height=8, progress_color=ACCENT, fg_color=TRACK)
            bar.set(count / peak)
            bar.pack(side="left", fill="x", expand=True, padx=6)

    def _clear_history(self) -> None:
        self.app.history.clear()
        self.rebuild()

    def on_show(self) -> None:
        self.rebuild()


class TrainingStudioPage(Page):
    title = "Training Studio"
    subtitle = "Record examples of your own hand poses, train a classifier on them, and check how good it is."

    def build(self) -> None:
        app = self.app
        dataset = app.dataset
        labels = dataset.labels()
        selected = getattr(self, "_selected_name", None)
        if selected not in labels:
            selected = labels[0] if labels else ""
        self._selected_name = selected

        box = self.section(
            "Camera",
            "Gesture control is switched off while this page is open, so the poses you record can't click or "
            "scroll anything. Only landmark coordinates are saved (data/gesture_samples.csv) - never images.",
        )
        self.preview_panel = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10, height=250)
        self.preview_panel.pack(fill="x", padx=18, pady=6)
        self.preview_panel.pack_propagate(False)
        self.preview_label = ctk.CTkLabel(self.preview_panel, text="Waiting for the camera…", text_color=TEXT_DIM)
        self.preview_label.pack(fill="both", expand=True)
        self.end(box)

        box = self.section("1 · Gestures", "Train poses the built-in rules don't already use - rock sign, four "
                                           "fingers, 'L' shape, 'call me' and so on.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._new_name = entry(row, "New gesture name, e.g. Rock", 240)
        self._new_name.pack(side="left", padx=(0, 8))
        button(row, "Create", self._create, "primary", 90).pack(side="left")
        self._choice = ctk.StringVar(value=selected)
        self._choice.trace_add("write", lambda *_: self._select(self._choice.get()))
        for name in labels:
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=3)
            ctk.CTkRadioButton(row, text=name, variable=self._choice, value=name, font=ctk.CTkFont(size=13),
                               text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT, border_color=TRACK,
                               ).pack(side="left", padx=12, pady=8)
            count = dataset.count(name)
            label(row, f"{count} samples", 12, color=GREEN if count >= 40 else (
                AMBER if count >= MIN_SAMPLES_TO_TRAIN else RED)).pack(side="left", padx=8)
            button(row, "Delete", lambda n=name: self._delete(n), "danger", 70).pack(side="right", padx=(4, 8))
            button(row, "Clear samples", lambda n=name: self._clear(n), "ghost", 110).pack(side="right", padx=4)
            button(row, "Rename", lambda n=name: self._rename(n), "ghost", 76).pack(side="right", padx=4)
        self._message = self.note(box, "")
        for warning in dataset.issues():
            self.note(box, "⚠  " + warning, AMBER)
        self.end(box)

        box = self.section("2 · Record", "Select a gesture above, press Start, then hold the pose and move your "
                                         "hand around a little - nearer, further, slightly tilted.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._start_button = button(row, "●  Start", self._start, "primary", 100)
        self._start_button.pack(side="left", padx=(0, 8))
        self._pause_button = button(row, "Pause", self._pause, "ghost", 90)
        self._pause_button.pack(side="left", padx=(0, 8))
        self._stop_button = button(row, "■  Stop", self._stop, "ghost", 90)
        self._stop_button.pack(side="left", padx=(0, 16))
        self._counter = label(row, "", 13, True)
        self._counter.pack(side="left")
        self._status = self.note(box, app.recording_status()[0] or
                                 (f"Selected: {selected}" if selected else "Create a gesture first."))
        self.end(box)

        box = self.section("3 · Train and evaluate", "A quarter of the samples is held back and used only to "
                                                     "measure the model. Figures describe this dataset, not "
                                                     "every lighting or user.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._train_button = button(row, "Train model", self._train, "primary", 130)
        self._train_button.pack(side="left", padx=(0, 12))
        self._progress = ctk.CTkProgressBar(row, height=8, width=260, progress_color=ACCENT, fg_color=TRACK)
        self._progress.set(0)
        self._progress.pack(side="left")
        self._train_message = self.note(box, "")
        classifier = app.gesture_engine.custom_classifier
        report = getattr(self, "_report", None) or (getattr(classifier, "report", None) if classifier else None)
        if report:
            self._show_report(box, report)
        self.end(box)

        box = self.section("Model versions", "Every training run is kept (the last five). If a new model is "
                                             "worse, switch back to an earlier one.")
        if not app.models.versions:
            self.note(box, "No model yet. Custom gestures are recognised only after training.")
        for info in reversed(app.models.versions):
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=3)
            accuracy = "accuracy n/a" if info["accuracy"] is None else f"accuracy {info['accuracy'] * 100:.0f}%"
            label(row, f"v{info['version']}", 13, True, width=40).pack(side="left", padx=(12, 4), pady=8)
            label(row, f"{info['created']}  ·  {', '.join(info['labels'])}  ·  {info['samples']} samples  ·  "
                       f"{accuracy}", 11, color=TEXT_DIM).pack(side="left")
            if info["version"] == app.models.active:
                label(row, "● Active", 11, True, color=GREEN).pack(side="right", padx=12)
            else:
                button(row, "Use this", lambda v=info["version"]: self._use(v), "ghost", 90).pack(
                    side="right", padx=8)
        self.end(box)
        self._was_recording = app.recording_status()[1]
        self._tick()

    def _show_report(self, box, report: dict) -> None:
        tiles = ctk.CTkFrame(box, fg_color="transparent")
        tiles.pack(fill="x", padx=14, pady=(4, 0))
        fmt = lambda value: "n/a" if value is None else f"{value * 100:.0f}%"
        for title, value in (("Accuracy", fmt(report["accuracy"])), ("Precision", fmt(report["precision"])),
                             ("Recall", fmt(report["recall"])),
                             ("Train / validation", f"{report['train_size']} / {report['validation_size']}")):
            tile = ctk.CTkFrame(tiles, fg_color=PANEL_ALT, corner_radius=10)
            tile.pack(side="left", fill="x", expand=True, padx=4)
            label(tile, title, 11, color=TEXT_DIM).pack(anchor="w", padx=12, pady=(8, 0))
            label(tile, value, 18, True).pack(anchor="w", padx=12, pady=(0, 8))
        if report["confusion"] is None:
            self.note(box, "With a single gesture there is nothing to confuse it with, so no accuracy is "
                           "reported. Untrained poses are still rejected by the distance check.")
            return
        names = report["labels"]
        label(box, "Confusion matrix - rows: the gesture shown, columns: what the model answered", 12, True).pack(
            anchor="w", padx=18, pady=(10, 2))
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(anchor="w", padx=18)
        label(grid, "", 11, width=110).grid(row=0, column=0)
        for column, name in enumerate(names):
            label(grid, name[:10], 11, True, width=78).grid(row=0, column=column + 1, padx=1)
        for r, name in enumerate(names):
            label(grid, name[:14], 11, True, width=110, anchor="w").grid(row=r + 1, column=0)
            for c, value in enumerate(report["confusion"][r]):
                right = r == c
                label(grid, str(value), 12, True, width=78, height=26, corner_radius=6,
                      color=GREEN if right and value else (RED if value and not right else TEXT_DIM),
                      fg_color=PANEL_ALT).grid(row=r + 1, column=c + 1, padx=1, pady=1)
        detail = ",   ".join(f"{name}: precision {info['precision'] * 100:.0f}%, recall {info['recall'] * 100:.0f}%"
                             for name, info in report["per_label"].items())
        self.note(box, detail)

    def _tick(self) -> None:
        if not self._counter.winfo_exists():
            return  # rebuilt: the new build runs its own loop
        if self.winfo_ismapped():
            status, recording = self.app.recording_status()
            rec = self.app._recording
            self._counter.configure(text=f"{len(rec['samples'])} samples" if rec else "")
            if recording:
                self._status.configure(text=status, text_color=AMBER)
                self._pause_button.configure(text="Resume" if rec and rec["paused"] else "Pause")
            elif self._was_recording:
                self.rebuild()  # the run ended (Stop, limit reached, or camera lost): show new counts
                return
            self._was_recording = recording
        self.after(200, self._tick)

    def _busy(self) -> bool:
        if self.app.recording_status()[1]:
            self._message.configure(text="Stop the recording first.", text_color=RED)
            return True
        return False

    def _select(self, name: str) -> None:
        self._selected_name = name
        if not self.app.recording_status()[1]:
            self._status.configure(text=f"Selected: {name}", text_color=TEXT_DIM)

    def _create(self) -> None:
        if self._busy():
            return
        try:
            self._selected_name = self.app.dataset.create(self._new_name.get())
        except (CustomGestureError, TrainingError) as exc:
            self._message.configure(text=str(exc), text_color=RED)
            return
        self.app.config_data["mappings"].setdefault(CUSTOM_PREFIX + self._selected_name, "none")
        self.rebuild()

    def _rename(self, name: str) -> None:
        if self._busy():
            return
        new = ctk.CTkInputDialog(text=f"New name for '{name}':", title="Rename gesture").get_input()
        if not new:
            return
        try:
            self._selected_name = self.app.rename_training_label(name, new)
        except (CustomGestureError, TrainingError) as exc:
            self._message.configure(text=str(exc), text_color=RED)
            return
        self.rebuild()
        self._message.configure(text="Renamed. Train again so the model uses the new name.", text_color=AMBER)

    def _clear(self, name: str) -> None:
        if not self._busy():
            self.app.dataset.clear_samples(name)
            self.rebuild()

    def _delete(self, name: str) -> None:
        if not self._busy():
            self.app.remove_training_label(name)
            self.rebuild()
            self._message.configure(text="Deleted. Train again so the model forgets it.", text_color=AMBER)

    def _start(self) -> None:
        if self.app.recording_status()[1]:
            return
        if not self._selected_name:
            self._status.configure(text="Create and select a gesture first.", text_color=RED)
            return
        try:
            self.app.start_recording(self._selected_name)
        except CustomGestureError as exc:
            self._status.configure(text=str(exc), text_color=RED)
            return
        self._was_recording = True

    def _pause(self) -> None:
        rec = self.app._recording
        if rec is not None:
            self.app.pause_recording(not rec["paused"])

    def _stop(self) -> None:
        self.app.stop_recording()  # the tick notices and rebuilds

    def _train(self) -> None:
        if self._busy():
            return
        self._train_button.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start()
        self._train_message.configure(text="Training…", text_color=AMBER)
        self.after(80, self._run_training)  # let the progress bar appear first

    def _run_training(self) -> None:
        try:
            self._report = self.app.train_model()
        except TrainingError as exc:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._progress.set(0)
            self._train_button.configure(state="normal")
            self._train_message.configure(text=str(exc), text_color=RED)
            return
        self.rebuild()
        self._progress.set(1)
        self._train_message.configure(
            text=f"Model v{self.app.models.active} trained and in use. Map the gestures to actions in Gesture "
                 "Studio.", text_color=GREEN)

    def _use(self, version: int) -> None:
        try:
            self.app.use_model(version)
        except TrainingError as exc:
            self._train_message.configure(text=str(exc), text_color=RED)
            return
        self._report = None
        self.rebuild()

    def on_show(self) -> None:
        self.rebuild()


class VoicePage(Page):
    title = "Voice"
    subtitle = "Speak commands. Recognised on this computer - no audio or text is sent anywhere."

    EXAMPLES = (
        ("Scrolling", "scroll down  ·  scroll up a little  ·  scroll down fast  ·  scroll this page slowly  ·  "
                      "keep scrolling down  ·  stop  ·  go to top  ·  go to bottom  ·  page down"),
        ("Tabs and windows", "change the tab  ·  next tab  ·  previous tab  ·  new tab  ·  close tab  ·  "
                             "switch app  ·  minimize  ·  restore  ·  go back  ·  go forward  ·  reload"),
        ("Applications", "open google chrome  ·  open safari  ·  open notes  ·  open <any installed application>"),
        ("Mouse and text", "click  ·  double click  ·  right click  ·  copy  ·  paste  ·  cut  ·  select all  ·  "
                           "undo  ·  redo  ·  delete  ·  zoom in  ·  zoom out"),
        ("Media and slides", "play  ·  pause  ·  next song  ·  previous song  ·  volume up  ·  volume down  ·  "
                             "mute  ·  next slide  ·  previous slide"),
        ("Control", "stop everything  ·  resume  ·  lock  ·  unlock"),
    )

    def build(self) -> None:
        app = self.app
        box = self.section(
            "Voice control",
            "Say the wake word, then a command - in one breath (\"computer, scroll down\") or as two phrases "
            "(\"computer\" … \"open google chrome\"). Speech without the wake word is ignored. It understands "
            "the commands below and natural variations of them; it is not a free-form assistant.",
        )
        self.option_switch(box, "voice_enabled", "Listen for voice commands",
                           "Turns the microphone on. The first time, the speech model (about 40 MB) is "
                           "downloaded and the system asks for microphone access.", on_change=app.set_voice)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        label(row, "Wake word", 13, True).pack(side="left")
        from .voice import WAKE_WORDS
        option_menu(row, list(WAKE_WORDS), app.config_data["voice_wake_word"],
                    lambda chosen: app.set_option("voice_wake_word", chosen), 170).pack(side="right")
        live = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
        live.pack(fill="x", padx=18, pady=6)
        self._state = label(live, "", 13, True)
        self._state.pack(anchor="w", padx=12, pady=(10, 0))
        self._heard = label(live, "", 12, color=TEXT_DIM, justify="left", wraplength=740)
        self._heard.pack(anchor="w", padx=12)
        self._result = label(live, "", 12, justify="left", wraplength=740)
        self._result.pack(anchor="w", padx=12, pady=(0, 10))
        self.end(box)

        box = self.section(
            "Assistant",
            "For requests that are more than a fixed command, your words are transcribed and worked out on "
            "this computer. Writing and questions use a language model running locally (Ollama) - slower than "
            "an online assistant, and its facts can be wrong, so read what it writes. It cannot browse the web, "
            "see your screen, or send anything: mails are opened as drafts for you to check and send.",
        )
        self.option_switch(box, "assistant_enabled", "Understand free requests",
                           "Off = only the fixed commands below. On = also \"open YouTube and search …\", "
                           "\"write a mail about …\", questions. Needs a speech model of about 150 MB.")
        self._ai = self.note(box, "")
        for title, phrases in (
            ("Web", "open youtube  ·  open gmail  ·  open youtube and search for <song>  ·  play <song> on youtube  ·  "
                    "search google for <anything>  ·  open <site> dot com"),
            ("Writing", "write a mail about <topic>  ·  write an email to <person> about <topic>  ·  "
                        "write a paragraph about <topic>  ·  type <exactly these words>"),
            ("Questions", "what is …  ·  how do I …  ·  explain …  ·  translate … (answered from the model's "
                          "own knowledge, shown here and spoken)"),
        ):
            label(box, title, 12, True).pack(anchor="w", padx=18, pady=(6, 0))
            self.note(box, phrases)
        targets = {"Gmail in the browser": "gmail", "Default mail app": "mail"}
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        label(row, "Open mail drafts in", 13, True).pack(side="left")
        option_menu(row, list(targets), next((k for k, v in targets.items() if v == app.config_data["email_target"]),
                                              "Gmail in the browser"),
                    lambda chosen: app.set_option("email_target", targets[chosen]), 190).pack(side="right")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        label(row, "Your name, for signing mails", 13, True).pack(side="left")
        self._name = entry(row, "e.g. Sachin Singh", 190)
        self._name.insert(0, app.config_data["user_name"])
        self._name.bind("<FocusOut>", lambda _e: app.set_option("user_name", self._name.get().strip()[:60]))
        self._name.bind("<Return>", lambda _e: app.set_option("user_name", self._name.get().strip()[:60]))
        self._name.pack(side="right")
        self.option_switch(box, "speak_replies", "Speak answers aloud", "Answers to questions are also read out.")
        self.end(box)

        box = self.section("What you can say", "After the wake word. Small words such as \"please\", \"the\" "
                                               "and \"this page\" are fine.")
        for title, phrases in self.EXAMPLES:
            label(box, title, 12, True).pack(anchor="w", padx=18, pady=(6, 0))
            self.note(box, phrases)
        self.end(box)

        box = self.section("Good to know")
        for line in (
            "\"Scroll down\" gives one push of about a screenful. \"Slowly\" or \"keep scrolling\" scrolls "
            "steadily until you say \"stop\" (at most 90 seconds).",
            "\"Open …\" starts applications installed in the Applications folders, or ones you added under "
            "System Controls → Applications. It cannot run commands or open files.",
            "Voice obeys the same rules as gestures: nothing happens while control is stopped (except "
            "\"resume\"), feature switches apply, and Practice mode shows what would happen without doing it.",
            "macOS: the microphone permission belongs to the app you launched TouchlessX from (Terminal, "
            "VS Code). If the header shows \"No sound\", allow it under System Settings → Privacy & Security → "
            "Microphone and restart.",
            "A quiet room and a normal speaking voice near the laptop work best. Music or a video playing "
            "nearby can be misheard as commands only if it also says the wake word.",
        ):
            self.note(box, "•  " + line)
        self.end(box)
        self._refresh()

    def _refresh(self) -> None:
        if not self._state.winfo_exists():
            return
        if self.winfo_ismapped():
            app = self.app
            state, detail = app.voice_status
            text, color = {
                "off": ("Off", TEXT_DIM), "loading": ("Loading the speech model…", AMBER),
                "listening": ("● Listening", GREEN), "silent": ("● No sound from the microphone", AMBER),
                "error": ("● " + (detail or "Error"), RED), "stopped": ("Off", TEXT_DIM),
            }.get(state, ("Starting…", TEXT_DIM))
            if app.voice is None:
                text, color = "Off", TEXT_DIM
            self._state.configure(text=text, text_color=color)
            if state == "thinking" and app.voice is not None:
                self._state.configure(text="● Working on it…", text_color=AMBER)
            self._heard.configure(text=f"Heard: \"{app.voice_heard}\"" if app.voice_heard else "")
            self._result.configure(text=app.voice_result)
            self._ticks = getattr(self, "_ticks", 0) + 1
            if self._ticks % 10 == 1:  # every few seconds: is the local AI there?
                status = app.local_ai.status()
                self._ai.configure(
                    text={"ready": f"● Local AI ready ({app.local_ai.model}).",
                          "no_model": f"● Ollama is running but the model is missing - run: ollama pull {app.local_ai.model}",
                          "offline": "● Local AI is not running. It is started automatically when first needed; "
                                     "if that fails, install Ollama (brew install ollama)."}[status],
                    text_color=GREEN if status == "ready" else AMBER)
        self.after(300, self._refresh)


class ClipboardPage(Page):
    title = "Clipboard"
    subtitle = "Select, copy, paste and delete by gesture, with a history of what you copied."

    TEXT_ACTIONS = ("copy", "cut", "paste", "paste_previous", "select_all", "delete", "undo", "redo")

    def build(self) -> None:
        app = self.app
        box = self.section("How to work with text")
        for line in (
            "Select: point at the start, pinch and keep it closed until the drag starts, move to the end, "
            "then open your fingers. Pinching twice quickly selects a word.",
            "Copy, paste, cut, delete, select all, undo and redo are actions - the table below shows which "
            "gesture each is on in the active profile. Change them in Gesture Studio.",
            "They go to whichever application is in front, exactly like the keyboard shortcuts.",
        ):
            self.note(box, "•  " + line)
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=8)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="text")
        for i, action in enumerate(self.TEXT_ACTIONS):
            gestures = [g for g in app.all_gestures() if app.action_controller.action_for(g) == action]
            row = ctk.CTkFrame(grid, fg_color=PANEL_ALT, corner_radius=8)
            row.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            label(row, action_label(action), 12, True, width=150, anchor="w").pack(side="left", padx=10, pady=6)
            label(row, ",  ".join(f"{gesture_glyph(g)} {gesture_label(g)}" for g in gestures) or "not mapped",
                  12, color=TEXT if gestures else TEXT_DIM).pack(side="left")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=(0, 4))
        button(row, "Change in Gesture Studio", lambda: app.show_page("Gesture Studio"), "ghost", 210).pack(side="left")
        self.end(box)

        box = self.section(
            "Clipboard history",
            "The last ten pieces of text you copied, newest first - from any application, by gesture or "
            "keyboard. Held in memory only: never saved to disk, gone when TouchlessX closes. Anything you "
            "copy appears here, including passwords, so switch it off if that worries you.",
        )
        self.option_switch(box, "clipboard_history", "Keep clipboard history", "Text only; items over 20,000 "
                           "characters are skipped.", on_change=self._toggle)
        if not app.clipboard.items:
            self.note(box, "Nothing copied yet.")
        for index, text in enumerate(app.clipboard.items):
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=3)
            label(row, "current" if index == 0 else f"{index} back", 11, True,
                  color=GREEN if index == 0 else TEXT_DIM, width=62).pack(side="left", padx=(10, 4), pady=8)
            label(row, app.clipboard.preview(text), 12, anchor="w").pack(side="left", fill="x", expand=True)
            if index:
                button(row, "Use", lambda t=text: self._use(t), "ghost", 60).pack(side="right", padx=8)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=8)
        button(row, "Clear history", self._clear, "ghost", 130).pack(side="left")
        self.note(box, "'Use' puts that text back on the clipboard, ready to paste. The 'Paste Previous Copy' "
                       "action pastes the item one back without opening this page.")
        self.end(box)

    def _toggle(self, on: bool) -> None:
        self.app.set_option("clipboard_history", on)
        self.rebuild()

    def _use(self, text: str) -> None:
        self.app.copy_to_clipboard(text)
        self.rebuild()

    def _clear(self) -> None:
        self.app.clipboard.clear()
        self.rebuild()

    def on_show(self) -> None:
        self.rebuild()


class ProfilesPage(Page):
    title = "Profiles"
    subtitle = "Each profile keeps its own gesture mappings, sensitivity and feature switches."

    def build(self) -> None:
        app = self.app
        store, active = app.profiles, app.config_data["active_profile"]

        box = self.section("Your profiles", "Switch here or from the Profile menu in the header. Settings you "
                                            "change are saved to the active profile automatically.")
        for name in store.names():
            profile = store.profiles[name] if name != active else app.config_data
            row = ctk.CTkFrame(box, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", padx=18, pady=4)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            head = ctk.CTkFrame(text, fg_color="transparent")
            head.pack(anchor="w")
            label(head, name, 14, True).pack(side="left")
            if name == active:
                label(head, "  ● Active", 11, True, color=GREEN).pack(side="left")
            on = [title for key, title, *_ in CATEGORIES if profile["enabled"].get(key)]
            mapped = sum(1 for action in profile["mappings"].values() if action != "none")
            label(text, f"{mapped} gestures mapped  ·  " + (", ".join(on) if on else "everything switched off"),
                  11, color=TEXT_DIM).pack(anchor="w")
            button(row, "Delete", lambda n=name: self._delete(n), "danger", 70).pack(side="right", padx=(4, 10))
            button(row, "Export", lambda n=name: self._export(n), "ghost", 70).pack(side="right", padx=4)
            button(row, "Duplicate", lambda n=name: self._duplicate(n), "ghost", 86).pack(side="right", padx=4)
            button(row, "Rename", lambda n=name: self._rename(n), "ghost", 76).pack(side="right", padx=4)
            if name != active:
                button(row, "Use", lambda n=name: app.switch_profile(n), "primary", 60).pack(side="right", padx=4)
        self._message = self.note(box, "")
        self.end(box)

        box = self.section("New profile")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        self._new_name = entry(row, "Name, e.g. Video editing", 240)
        self._new_name.pack(side="left", padx=(0, 8))
        button(row, "Create from current settings", self._create, "primary", 220).pack(side="left", padx=(0, 8))
        button(row, "Import…", self._import, "ghost", 100).pack(side="left")
        self.note(box, "Imported files are checked: unknown gestures and actions are dropped, values are kept "
                       "within their normal ranges, and shortcuts or smart-home devices are only kept if you "
                       "have set up one with the same name on this computer.")
        self.end(box)

    def _fail(self, exc: Exception) -> None:
        self._message.configure(text=str(exc), text_color=RED)

    def _done(self, text: str) -> None:
        self.app.refresh_profiles()
        self.rebuild()
        self._message.configure(text=text, text_color=GREEN)

    def _create(self) -> None:
        try:
            name = self.app.profiles.create(self._new_name.get(), self.app.config_data)
        except ProfileError as exc:
            return self._fail(exc)
        self._done(f"Created '{name}'. Press Use to switch to it.")

    def _duplicate(self, name: str) -> None:
        if name == self.app.config_data["active_profile"]:
            self.app.profiles.set(name, self.app.config_data)  # include edits not yet written
        self._done(f"Created '{self.app.profiles.duplicate(name)}'.")

    def _rename(self, name: str) -> None:
        dialog = ctk.CTkInputDialog(text=f"New name for '{name}':", title="Rename profile")
        new = dialog.get_input()
        if not new:
            return
        try:
            new = self.app.profiles.rename(name, new)
        except ProfileError as exc:
            return self._fail(exc)
        if name == self.app.config_data["active_profile"]:
            self.app.set_option("active_profile", new)
        self._done(f"Renamed to '{new}'.")

    def _delete(self, name: str) -> None:
        app = self.app
        others = [n for n in app.profiles.names() if n != name]
        if not others:
            return self._fail(ProfileError("At least one profile has to remain."))
        if name == app.config_data["active_profile"]:
            app.switch_profile(others[0])  # rebuilds this page
        app.profiles.delete(name)
        app.refresh_profiles()
        page = app.current_page_widget()
        if isinstance(page, ProfilesPage):
            page.rebuild()
            page._message.configure(text=f"Deleted '{name}'.", text_color=GREEN)

    def _export(self, name: str) -> None:
        if name == self.app.config_data["active_profile"]:
            self.app.profiles.set(name, self.app.config_data)
        path = filedialog.asksaveasfilename(
            parent=self.app, title="Export profile", defaultextension=".json",
            initialfile=f"{name}.touchlessx.json", filetypes=[("TouchlessX profile", "*.json")])
        if not path:
            return
        try:
            self.app.profiles.export_to(name, path)
        except OSError as exc:
            return self._fail(exc)
        self._message.configure(text=f"Exported to {path}", text_color=GREEN)

    def _import(self) -> None:
        path = filedialog.askopenfilename(parent=self.app, title="Import profile",
                                          filetypes=[("TouchlessX profile", "*.json")])
        if not path:
            return
        try:
            name = self.app.profiles.import_from(
                path, shortcuts=list(self.app.config_data["shortcuts"]), smart_devices=self.app.smart_home.names(),
                apps=list(self.app.config_data["apps"]))
        except ProfileError as exc:
            return self._fail(exc)
        self._done(f"Imported as '{name}'. Press Use to switch to it.")

    def on_show(self) -> None:
        self.rebuild()


class SettingsPage(Page):
    title = "Settings"
    subtitle = "General & privacy."

    RESOLUTIONS = {"640 × 360": (640, 360), "960 × 540": (960, 540), "1280 × 720": (1280, 720)}
    THEMES = {"Dark": "dark", "Light": "light", "Match system": "system"}
    RETENTION = {"7 days": 7, "30 days": 30, "90 days": 90, "1 year": 365}
    HANDS = {"Either hand": "any", "Right hand only": "right", "Left hand only": "left"}

    def _menu_row(self, parent, title: str, values: list[str], current: str, command, hint: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left")
        label(text, title, 13, True).pack(anchor="w")
        if hint:
            label(text, hint, 11, color=TEXT_DIM).pack(anchor="w")
        menu = option_menu(row, values, current, command, 170)
        menu.pack(side="right")
        return menu

    def build(self) -> None:
        app = self.app
        config = app.config_data

        box = self.section("Appearance")
        theme = next((k for k, v in self.THEMES.items() if v == config["theme"]), "Dark")
        self._menu_row(box, "Theme", list(self.THEMES), theme,
                       lambda chosen: app.set_option("theme", self.THEMES[chosen]))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=7)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left")
        label(text, "Accent colour", 13, True).pack(anchor="w")
        label(text, "Buttons, switches, the selected page and highlights. Text on it turns dark or light "
                    "by itself to stay readable.", 11, color=TEXT_DIM).pack(anchor="w")
        current = config["accent_color"]
        button(row, "Custom…", self._pick_accent, "ghost", 86).pack(side="right", padx=(8, 0))
        for name, color in reversed(ACCENT_PRESETS.items()):
            chosen = color == current
            ctk.CTkButton(
                row, text="✓" if chosen else "", width=30, height=30, corner_radius=15, fg_color=color,
                hover_color=color, text_color=text_on(color), border_width=2 if chosen else 0,
                border_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda c=color: self._set_accent(c),
            ).pack(side="right", padx=3)
        if current not in ACCENT_PRESETS.values():
            label(row, current, 11, color=TEXT_DIM).pack(side="right", padx=6)
        self.option_switch(box, "mini_controller", "Floating mini controller",
                           "A small window that stays on top of other apps with the current state and a Pause "
                           "button. Minimise TouchlessX and keep this in view.",
                           on_change=app.set_mini_controller)
        self.end(box)

        box = self.section("Camera")
        self._camera = self._menu_row(box, "Camera", [f"Camera {i}" for i in range(4)],
                                      f"Camera {config.get('camera_index', 0)}", lambda _c: None,
                                      "Camera 0 is usually the built-in one.")
        current = next((k for k, v in self.RESOLUTIONS.items()
                        if v == (config.get("frame_width"), config.get("frame_height"))), "960 × 540")
        self._resolution = self._menu_row(box, "Resolution", list(self.RESOLUTIONS), current, lambda _c: None,
                                          "Lower is faster; higher tracks a distant hand better.")
        self.option_switch(box, "show_landmarks", "Show hand landmarks",
                           "Draw the tracked hand skeleton on the preview.")
        self.option_switch(box, "mirror_preview", "Mirror the preview",
                           "Show yourself as in a mirror. Only the picture changes; control stays the same.")
        self.option_switch(box, "ignore_resting_hand", "Ignore a resting hand",
                           "A hand at the bottom edge of the picture with its wrist out of view - resting on "
                           "the desk or keyboard - controls nothing. Raise your hand so the wrist is visible "
                           "to use gestures.")
        hand = next((k for k, v in self.HANDS.items() if v == config["preferred_hand"]), "Either hand")
        self._menu_row(box, "Controlling hand", list(self.HANDS), hand,
                       lambda chosen: app.set_option("preferred_hand", self.HANDS[chosen]),
                       "For left- or right-handed use: the other hand is ignored completely.")
        button(box, "Apply & restart camera", self._apply_camera, "primary", 190).pack(anchor="w", padx=18, pady=6)
        self.end(box)

        box = self.section(
            "Emergency stop",
            "The red EMERGENCY STOP button on the dashboard, Space, Esc and the mini controller always work. "
            "You can also choose one gesture that stops everything in every profile, even while locked.",
        )
        gestures = {"None": "none", **{gesture_label(g): g for g in app.all_gestures() if g != "pinch"}}
        current = next((k for k, v in gestures.items() if v == config["emergency_gesture"]), "None")
        self._menu_row(box, "Emergency stop gesture", list(gestures), current,
                       lambda chosen: (app.set_option("emergency_gesture", gestures[chosen]), app._sync_gestures()),
                       "It no longer performs its mapped action. Resume from the button.")
        self.end(box)

        box = self.section(
            "Privacy & data",
            "Video is processed on this computer, frame by frame, and is never saved or uploaded. There is no "
            "telemetry. The only network use is the one-time hand-model download and any smart-home webhooks "
            "you add.",
        )
        self.option_switch(box, "history_enabled", "Keep action history",
                           "Time, gesture, action and confidence of each performed action, in data/history.db. "
                           "Feeds the AI Insights page.")
        keep = next((k for k, v in self.RETENTION.items() if v == config["history_retention_days"]), "30 days")
        self._menu_row(box, "Keep history for", list(self.RETENTION), keep,
                       lambda chosen: app.set_option("history_retention_days", self.RETENTION[chosen]),
                       "Older entries are deleted automatically.")
        self.option_switch(box, "action_log_enabled", "Keep a diagnostic log",
                           "For troubleshooting: performed actions in logs/actions.log, and in logs/frames.csv "
                           "the hand's 21 landmark points with how each frame was classified (the last few "
                           "minutes only). Coordinates, never images.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=6)
        button(row, "Clear history", self._clear_history, "ghost", 120).pack(side="left", padx=(0, 8))
        button(row, "Clear log", self._clear_log, "ghost", 100).pack(side="left", padx=(0, 8))
        button(row, "Open log folder", self._open_logs, "ghost", 140).pack(side="left")
        self._log_message = label(row, "", 11, color=TEXT_DIM)
        self._log_message.pack(side="left", padx=10)
        self.end(box)

        box = self.section("Startup and updates")
        self._login = ctk.BooleanVar(value=app.login_item.is_enabled())
        self._login.trace_add("write", lambda *_: self._system_message.configure(
            text=app.set_start_at_login(self._login.get()), text_color=TEXT_DIM))
        packaged = is_packaged()
        switch_row(box, "Start TouchlessX when I log in",
                   "Adds a per-user login entry; no administrator rights needed." + ("" if packaged else
                   " From source on macOS the camera and microphone permissions belong to the program that "
                   "starts it, so a login start may come up without camera access - the packaged app does "
                   "not have this problem."), self._login)
        if platform.system() == "Darwin":
            self.option_switch(box, "menu_bar_icon", "Menu-bar icon",
                               "A ✋ in the menu bar to show the window, pause, stop the camera or quit.",
                               on_change=app.set_menu_bar)
            self.option_switch(box, "close_to_menu_bar", "Keep running when the window is closed",
                               "Closing the window then only hides it; use the menu-bar icon to bring it back "
                               "or to quit. Needs the menu-bar icon.")
        self.option_switch(box, "check_updates", "Check for updates at start",
                           "Asks GitHub once per start whether a newer release exists. Nothing is installed "
                           "by itself: you get a button that opens the download page.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=6)
        button(row, "Check now", self._check_updates, "ghost", 110).pack(side="left")
        self._system_message = label(row, self._update_text(app.update_info), 11, color=TEXT_DIM)
        self._system_message.pack(side="left", padx=10)
        self.end(box)

        box = self.section("Help")
        self.note(box, "Space / Esc pause control while TouchlessX is in front. The setup guide explains "
                       "permissions, gestures and the emergency pause.")
        button(box, "Open the setup guide", lambda: app.show_page("Getting Started"), "ghost", 190).pack(
            anchor="w", padx=18, pady=8)
        self.end(box)

        box = self.section("About")
        label(box, app.versions_text(), 12, color=TEXT_DIM, justify="left").pack(anchor="w", padx=18)
        self.note(box, "Local build - no account, licence activation or update service. See docs/ for the user "
                       "guide and QA checklist.")
        button(box, "Reset active profile to defaults", app.reset_settings, "danger", 250).pack(
            anchor="w", padx=18, pady=8)
        self.end(box)

    @staticmethod
    def _update_text(info) -> str:
        if not info:
            return ""
        return {"update": f"Version {info['latest']} is available - use the Update button in the header.",
                "current": "You have the latest version.", "none": "No release has been published yet.",
                "error": "Could not reach GitHub to check."}[info["status"]]

    def _check_updates(self) -> None:
        self._system_message.configure(text="Checking…")
        message = self._system_message
        self.app.check_updates(lambda info: message.winfo_exists() and message.configure(text=self._update_text(info)))

    def _set_accent(self, color: str) -> None:
        self.app.set_option("accent_color", color)
        self.rebuild()  # moves the tick to the chosen swatch

    def _pick_accent(self) -> None:
        _, color = colorchooser.askcolor(color=self.app.config_data["accent_color"], parent=self.app,
                                         title="Choose an accent colour")
        if color:
            self._set_accent(color)

    def _apply_camera(self) -> None:
        width, height = self.RESOLUTIONS[self._resolution.get()]
        self.app.restart_camera(int(self._camera.get().split()[-1]), width, height)

    def _open_logs(self) -> None:
        folder = self.app.log_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
            elif platform.system() == "Windows":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            self._log_message.configure(text=f"Could not open {folder}: {exc}")
            print(f"Log folder: {folder}", file=sys.stderr)

    def _clear_log(self) -> None:
        self.app.action_log.clear()
        self.app.frame_log.clear()
        self._log_message.configure(text="Log cleared.")

    def _clear_history(self) -> None:
        self.app.history.clear()
        self._log_message.configure(text="History cleared.")


class OnboardingPage(Page):
    title = "Getting Started"
    subtitle = "Four things to know before you let your hand take over."

    def build(self) -> None:
        app = self.app
        box = self.section("1 · Camera")
        self._camera = self.note(box, "")
        self.note(box, "The first start asks for camera access. On macOS the permission belongs to the app you "
                       "launched TouchlessX from. Frames are processed here and never saved or uploaded.")
        self.end(box)

        box = self.section("2 · Permission to control the mouse and keyboard")
        self._input = self.note(box, "")
        button(box, "Request permission", app.executor.request_permission, "ghost", 170).pack(
            anchor="w", padx=18, pady=(8, 0))
        self.end(box)

        box = self.section("3 · The gestures", "Sit 40-80 cm from the camera with your palm facing it, in "
                                               "reasonable light. This is the active profile's mapping:")
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=4)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="gestures")
        shown = [g for g in app.all_gestures() if app.action_controller.action_for(g) != "none"]
        for i, gesture in enumerate(shown):
            row = ctk.CTkFrame(grid, fg_color=PANEL_ALT, corner_radius=8)
            row.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            label(row, gesture_glyph(gesture), 18, width=34).pack(side="left", padx=(8, 0), pady=6)
            label(row, gesture_label(gesture), 12, True, width=120, anchor="w").pack(side="left", padx=6)
            label(row, action_label(app.action_controller.action_for(gesture)), 12, color=TEXT_DIM).pack(side="left")
        self.note(box, "Pinch twice quickly to double-click. One-shot poses such as the fist must be held for a "
                       "moment before they act; scroll and volume start after a short hold. Try everything "
                       "safely first with Practice mode in Gesture Studio.")
        self.end(box)

        box = self.section("4 · Stopping it")
        self.note(box, "•  Space or Esc while TouchlessX is in front\n"
                       "•  The Pause button in the header, or on the floating mini controller that stays on top "
                       "of other apps\n"
                       "•  Stop Camera turns tracking off completely\n"
                       "Not for vehicles, medical devices, machinery or anything else safety-critical.")
        self.end(box)

        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=10)
        button(row, "Start using TouchlessX", app.finish_onboarding, "primary", 220).pack(side="left", padx=(0, 8))
        button(row, "Practise first", self._practise, "ghost", 140).pack(side="left")
        self._refresh()

    def _practise(self) -> None:
        self.app.set_practice(True)
        self.app.config_data["onboarded"] = True
        self.app.show_page("Gesture Studio")

    def _refresh(self) -> None:
        if not self._camera.winfo_exists():
            return
        if self.winfo_ismapped():
            running = self.app.camera_running
            self._camera.configure(
                text="● Camera is running." if running else "● Camera is not running yet - allow access if "
                                                             "asked; TouchlessX retries automatically.",
                text_color=GREEN if running else AMBER)
            ok = self.app.input_ok()
            self._input.configure(
                text="● Granted - gestures can move the mouse and press keys." if ok else
                     "● Not granted yet. macOS: System Settings → Privacy & Security → Accessibility → enable "
                     "the app you launched from, then restart TouchlessX. Until then gestures are only "
                     "detected.", text_color=GREEN if ok else AMBER)
        self.after(1000, self._refresh)

    def on_show(self) -> None:
        self.rebuild()


PAGES = {
    "Gesture Studio": GestureStudioPage,
    "System Controls": SystemControlsPage,
    "Media & Presentation": MediaPage,
    "Smart Home": SmartHomePage,
    "Training Studio": TrainingStudioPage,
    "AI Insights": InsightsPage,
    "Voice": VoicePage,
    "Clipboard": ClipboardPage,
    "Profiles": ProfilesPage,
    "Settings": SettingsPage,
    "Getting Started": OnboardingPage,
}

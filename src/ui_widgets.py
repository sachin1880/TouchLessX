"""Shared look-and-feel for the UI: palette, gesture display names, small widget helpers."""
from __future__ import annotations

import customtkinter as ctk

from .colors import DEFAULT_ACCENT, accent_palette, normalize_hex
from .gesture_engine import CUSTOM_PREFIX

# Every colour is a (light theme, dark theme) pair; CustomTkinter picks the right one
# and re-colours widgets when the theme changes. Use `resolve` for plain Tk widgets.
BG = ("#eef2f8", "#070c18")
PANEL = ("#ffffff", "#0f182b")
PANEL_ALT = ("#f1f5fb", "#0a1221")
BORDER = ("#d3dbe8", "#1c2942")
TRACK = ("#c9d3e3", "#2a3550")
# The accent colours are lists, not tuples, because the user can change them: every
# widget keeps a reference to these same lists, so `set_accent` updates them in place
# and a redraw is all it takes for the whole interface to follow.
ACCENT = ["#2f7df6", "#2f7df6"]
ACCENT_HOVER = ["#2466cf", "#2466cf"]
ACCENT_SOFT = ["#d6e6ff", "#12305f"]
ON_ACCENT = ["#ffffff", "#ffffff"]
GREEN = ("#0e9467", "#27d79a")
RED = ("#d6363c", "#ff5a5f")
AMBER = ("#a86e00", "#f5a524")
PURPLE = ("#7446d8", "#8b5cf6")
TEXT = ("#152033", "#e8edf5")
TEXT_DIM = ("#5a6679", "#8794ab")
DANGER_BG = ("#fde4e5", "#3a1820")
DANGER_HOVER = ("#f9cdd0", "#5a222c")
DANGER_TEXT = ("#b3262c", "#ff8a8e")


def set_accent(color) -> str:
    """Switches the accent colour (falls back to the default for an invalid value) and
    returns the colour now in use. Existing widgets need a redraw afterwards."""
    try:
        color = normalize_hex(color)
    except ValueError:
        color = DEFAULT_ACCENT
    palette = accent_palette(color, PANEL[0], PANEL[1])
    for target, key in ((ACCENT, "accent"), (ACCENT_HOVER, "hover"), (ACCENT_SOFT, "soft"), (ON_ACCENT, "on")):
        target[:] = palette[key]
    return color


def resolve(color) -> str:
    """The concrete colour of a (light, dark) pair under the current theme."""
    if isinstance(color, str):
        return color
    return color[0] if ctk.get_appearance_mode() == "Light" else color[1]


# gesture id -> (display name, glyph)
GESTURE_INFO = {
    "open_palm": ("Open Palm", "✋"),
    "point": ("Point", "☝"),
    "three_fingers": ("Three Fingers", "🤟"),
    "pinch": ("Pinch", "🤏"),
    "two_fingers": ("Two Fingers", "✌"),
    "fist": ("Fist", "✊"),
    "swipe_left": ("Swipe Left", "⇦"),
    "swipe_right": ("Swipe Right", "⇨"),
    "thumbs_up": ("Thumbs Up", "👍"),
    "swipe_up": ("Swipe Up", "⇧"),
    "swipe_down": ("Swipe Down", "⇩"),
    "hand_open": ("Open From Fist", "🖐"),
    "point_left": ("Point Left", "👈"),
    "point_right": ("Point Right", "👉"),
    "unknown": ("Unknown", "?"),
    "none": ("No Hand", "–"),
    "voice": ("Voice", "🎙"),
    "resting": ("Resting - raise your hand", "⤓"),
}

# (config key, display label, glyph, tint)
CATEGORIES = [
    ("mouse_control", "Mouse Control", "➤", ACCENT),
    ("media_control", "Media Control", "♫", PURPLE),
    ("presentation", "Presentation", "▣", GREEN),
    ("volume_control", "Volume Control", "◖", ACCENT),
    ("smart_home", "Smart Home", "⌂", AMBER),
    ("ai_assistant", "AI Assistant", "✦", PURPLE),
]


def gesture_label(gesture: str) -> str:
    if gesture.startswith(CUSTOM_PREFIX):
        return gesture[len(CUSTOM_PREFIX):]
    return GESTURE_INFO.get(gesture, (gesture, ""))[0]


def gesture_glyph(gesture: str) -> str:
    if gesture.startswith(CUSTOM_PREFIX):
        return "★"
    return GESTURE_INFO.get(gesture, ("", "?"))[1]


def font(size: int = 12, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def card(parent, **kwargs) -> ctk.CTkFrame:
    options = dict(fg_color=PANEL, corner_radius=14, border_width=1, border_color=BORDER)
    options.update(kwargs)
    return ctk.CTkFrame(parent, **options)


def label(parent, text: str, size: int = 12, bold: bool = False, color: str = TEXT, **kwargs) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=font(size, bold), text_color=color, **kwargs)


def button(parent, text: str, command, kind: str = "primary", width: int = 120, **kwargs) -> ctk.CTkButton:
    colors = {
        "primary": (ACCENT, ACCENT_HOVER, ON_ACCENT),
        "ghost": (PANEL_ALT, BORDER, TEXT),
        "danger": (DANGER_BG, DANGER_HOVER, DANGER_TEXT),
    }[kind]
    return ctk.CTkButton(
        parent, text=text, command=command, width=width, height=32, corner_radius=8,
        fg_color=colors[0], hover_color=colors[1], text_color=colors[2],
        border_width=1 if kind != "primary" else 0, border_color=BORDER, font=font(12, True), **kwargs,
    )


def switch(parent, variable, text: str = "") -> ctk.CTkSwitch:
    return ctk.CTkSwitch(
        parent, text=text, variable=variable, onvalue=True, offvalue=False, width=44,
        progress_color=ACCENT, fg_color=TRACK, button_color=ON_ACCENT, button_hover_color=ON_ACCENT,
        font=font(12), text_color=TEXT,
    )


def setting_row(parent, title: str, hint: str = "") -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """A row with title/hint text on the left; returns (row, right-hand slot for a control)."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=18, pady=7)
    text = ctk.CTkFrame(row, fg_color="transparent")
    text.pack(side="left", fill="x", expand=True)
    label(text, title, 13, True).pack(anchor="w")
    if hint:
        label(text, hint, 11, color=TEXT_DIM, justify="left", wraplength=520).pack(anchor="w")
    slot = ctk.CTkFrame(row, fg_color="transparent")
    slot.pack(side="right", padx=(12, 0))
    return row, slot


def slider_row(parent, title: str, hint: str, value: float, low: float, high: float,
               on_change, fmt: str = "{:.2f}", steps: int = 100) -> ctk.CTkSlider:
    _, slot = setting_row(parent, title, hint)
    value_label = label(slot, fmt.format(value), 12, True, width=56)
    value_label.pack(side="right")

    def changed(new_value: float) -> None:
        value_label.configure(text=fmt.format(new_value))
        on_change(float(new_value))

    slider = ctk.CTkSlider(
        slot, from_=low, to=high, number_of_steps=steps, width=220, command=changed,
        progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER, fg_color=TRACK,
    )
    slider.set(min(high, max(low, value)))
    slider.pack(side="right", padx=(0, 8))
    return slider


def switch_row(parent, title: str, hint: str, variable) -> ctk.CTkSwitch:
    _, slot = setting_row(parent, title, hint)
    control = switch(slot, variable)
    control.pack(side="right")
    return control


def option_menu(parent, values: list[str], current: str, command, width: int = 220) -> ctk.CTkOptionMenu:
    menu = ctk.CTkOptionMenu(
        parent, values=values, command=command, width=width, height=30, corner_radius=8,
        fg_color=PANEL_ALT, button_color=BORDER, button_hover_color=TRACK,
        dropdown_fg_color=PANEL, dropdown_hover_color=ACCENT_SOFT, dropdown_text_color=TEXT,
        text_color=TEXT, font=font(12), dropdown_font=font(12), dynamic_resizing=False,
    )
    menu.set(current)
    return menu


def entry(parent, placeholder: str, width: int = 200, show: str = "") -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent, placeholder_text=placeholder, width=width, height=32, corner_radius=8, show=show,
        fg_color=PANEL_ALT, border_color=BORDER, text_color=TEXT, placeholder_text_color=TEXT_DIM, font=font(12),
    )

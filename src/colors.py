"""Colour arithmetic for the customisable accent colour. No UI imports, so it is testable."""
from __future__ import annotations

DEFAULT_ACCENT = "#2f7df6"

# name -> hex, in the order they are offered
ACCENT_PRESETS = {
    "Blue": "#2f7df6",
    "Indigo": "#5b5bf0",
    "Purple": "#8b5cf6",
    "Pink": "#e0499b",
    "Red": "#e5484d",
    "Orange": "#f08a24",
    "Green": "#1fa971",
    "Teal": "#12a5b8",
}


def normalize_hex(text) -> str:
    """'#2F7', '2f7df6', ' #2F7DF6 ' -> '#2f7df6'. Raises ValueError for anything else."""
    value = str(text).strip().lstrip("#").lower()
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"'{text}' is not a colour like #2f7df6")
    return "#" + value


def _rgb(color: str) -> tuple[int, int, int]:
    color = normalize_hex(color)
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(color: str, other: str, amount: float) -> str:
    """`color` moved `amount` (0..1) of the way towards `other`."""
    a, b = _rgb(color), _rgb(other)
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def luminance(color: str) -> float:
    """Relative luminance (WCAG), 0 = black, 1 = white."""
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(color: str, other: str) -> float:
    high, low = sorted((luminance(color), luminance(other)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def text_on(color: str) -> str:
    """White where white is readable (the usual look for a coloured button), otherwise
    near-black - which is what light colours such as yellow or orange need."""
    return "#ffffff" if contrast(color, "#ffffff") >= 3.0 else "#10151f"


def accent_palette(color: str, light_surface: str = "#ffffff", dark_surface: str = "#0f182b") -> dict:
    """Everything the interface derives from one accent colour, as (light theme, dark theme) pairs."""
    accent = normalize_hex(color)
    hover = mix(accent, "#000000", 0.16)
    return {
        "accent": (accent, accent),
        "hover": (hover, hover),
        "soft": (mix(accent, light_surface, 0.82), mix(accent, dark_surface, 0.72)),  # tinted backgrounds
        "on": (text_on(accent), text_on(accent)),
    }

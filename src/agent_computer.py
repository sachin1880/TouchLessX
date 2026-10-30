"""Computer tool layer for the multi-task agent.

Each function performs ONE concrete OS action and returns a short result string
that is fed back to the AI so it knows what happened.

Design constraints
──────────────────
* type_text() goes through the macOS clipboard (pbcopy → cmd+v) so Hindi,
  emoji and any Unicode work correctly — pyautogui.typewrite only handles ASCII.
* screenshot() tries PIL ImageGrab first, falls back to macOS screencapture.
* All blocking calls are kept short; the agent loop runs in a worker thread.
* pyautogui is imported lazily (not available in test environments).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
import webbrowser
from urllib.parse import quote, quote_plus


# ── helpers ───────────────────────────────────────────────────────────────────

def _pag():
    """Lazy import of pyautogui (not available in headless test runs)."""
    import pyautogui as _p
    _p.PAUSE = 0.08
    _p.FAILSAFE = False
    return _p


def _encode(text: str) -> str:
    """URL-encode text including Unicode (Hindi etc.)."""
    return quote(text, safe="")


# ── tools ─────────────────────────────────────────────────────────────────────

def open_url(url: str) -> str:
    """Open *url* in the default browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    time.sleep(0.4)
    return f"opened: {url[:80]}"


def open_app(name: str) -> str:
    """Launch a macOS application by name."""
    try:
        subprocess.Popen(["open", "-a", name])
        time.sleep(1.0)
        return f"launched: {name}"
    except FileNotFoundError:
        return f"app not found: {name}"


def type_text(text: str) -> str:
    """Paste *text* at the current cursor position via the clipboard.

    Uses pbcopy + Cmd+V so Hindi / emoji / Unicode all work.
    """
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        time.sleep(0.15)
        _pag().hotkey("cmd", "v")
        time.sleep(0.12)
        return f"typed ({len(text)} chars)"
    except Exception as exc:
        return f"type failed: {exc}"


def press_key(keys: str) -> str:
    """Press a keyboard shortcut such as 'enter', 'cmd+t', 'cmd+shift+n'.

    Keys are split on '+' and passed to pyautogui.hotkey.
    """
    parts = [k.strip().lower() for k in keys.split("+")]
    try:
        _pag().hotkey(*parts)
        time.sleep(0.12)
        return f"pressed: {keys}"
    except Exception as exc:
        return f"key failed: {exc}"


def scroll(amount: int = 3) -> str:
    """Scroll the active window. Positive = down, negative = up."""
    try:
        _pag().scroll(-amount)
        time.sleep(0.1)
        return f"scrolled {'down' if amount > 0 else 'up'} {abs(amount)}"
    except Exception as exc:
        return f"scroll failed: {exc}"


def click(x: int, y: int) -> str:
    """Click at absolute screen co-ordinates."""
    try:
        _pag().click(x, y)
        time.sleep(0.1)
        return f"clicked ({x}, {y})"
    except Exception as exc:
        return f"click failed: {exc}"


def wait(seconds: float = 1.5) -> str:
    """Pause execution for up to 8 seconds."""
    secs = min(float(seconds), 8.0)
    time.sleep(secs)
    return f"waited {secs:.1f}s"


def screenshot() -> str:
    """Take a full-screen screenshot and return the temp-file path."""
    path = tempfile.mktemp(suffix=".png")
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(path)
        return path
    except Exception:
        try:
            subprocess.run(["screencapture", "-x", path], check=True)
            return path
        except Exception as exc:
            return f"screenshot failed: {exc}"


def get_clipboard() -> str:
    """Return the current clipboard contents (text only)."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return result.stdout.strip()[:400]
    except Exception:
        return ""


# ── convenience URL builders (used by the planner's system prompt) ─────────────

def whatsapp_url(message: str, phone: str = "") -> str:
    """Build a WhatsApp Web URL with a pre-filled message."""
    base = "https://web.whatsapp.com/send"
    params = f"?text={_encode(message)}"
    if phone:
        params = f"?phone={quote(phone)}&text={_encode(message)}"
    return base + params


def gmail_compose_url(subject: str = "", body: str = "", to: str = "") -> str:
    """Build a Gmail compose URL."""
    return (
        "https://mail.google.com/mail/?view=cm&fs=1"
        + (f"&to={_encode(to)}" if to else "")
        + (f"&su={_encode(subject)}" if subject else "")
        + (f"&body={_encode(body)}" if body else "")
    )


# ── dispatcher (called by the planner loop) ───────────────────────────────────

def execute(action: str, args: dict) -> str:
    """Route an action name + args dict to the matching tool function."""
    if   action == "open_url":   return open_url(args.get("url", ""))
    elif action == "open_app":   return open_app(args.get("name", ""))
    elif action == "type_text":  return type_text(args.get("text", ""))
    elif action == "press_key":  return press_key(args.get("keys", "enter"))
    elif action == "scroll":     return scroll(int(args.get("amount", 3)))
    elif action == "click":      return click(int(args.get("x", 0)), int(args.get("y", 0)))
    elif action == "wait":       return wait(float(args.get("seconds", 1.5)))
    elif action == "screenshot": return screenshot()
    elif action == "speak":      return "spoken"    # handled by caller
    elif action == "done":       return "done"      # handled by caller
    else:                        return f"unknown action: {action}"

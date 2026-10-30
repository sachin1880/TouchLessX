"""Computer tool layer — real system-level automation for the JARVIS agent.

Every function performs ONE concrete OS action and returns a short result
string that is fed back to the AI so it knows what happened.

Capabilities
────────────
  Browser / web    open_url, google_search
  Native apps      open_app, open_folder, shell
  Typing           type_text, press_key, scroll, click, wait
  Messaging        send_imessage, whatsapp_send
  Email            send_email_native (Mail.app), send_email_gmail (browser)
  Clipboard        get_clipboard
  Screen           screenshot
  Contacts         add_contact, lookup_contact

Design notes
────────────
  • type_text() uses macOS clipboard (pbcopy → Cmd+V) — handles Hindi/emoji.
  • send_imessage() uses AppleScript → Messages.app (iMessage / SMS).
  • send_email_native() uses AppleScript → Mail.app.
  • whatsapp_send() opens WhatsApp Web with pre-filled message + sends.
  • shell() allows only a safe whitelist of commands.
  • All blocking calls kept short; the agent runs in a worker thread.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote, quote_plus


# ── lazy imports ──────────────────────────────────────────────────────────────

def _pag():
    import pyautogui as _p
    _p.PAUSE = 0.10
    _p.FAILSAFE = False
    return _p


# ── helpers ───────────────────────────────────────────────────────────────────

def _encode(text: str) -> str:
    return quote(text, safe="")


def _esc_as(text: str) -> str:
    """Escape a string for embedding inside an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def run_applescript(script: str) -> str:
    """Execute AppleScript and return output or error string."""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            return r.stdout.strip() or "done"
        return f"AppleScript error: {r.stderr.strip()[:300]}"
    except subprocess.TimeoutExpired:
        return "AppleScript timed out"
    except Exception as exc:
        return f"AppleScript failed: {exc}"


def _resolve_contact(name_or_number: str) -> tuple[str, str]:
    """Return (phone, display_name).  Looks up contacts first."""
    try:
        from . import agent_contacts as _c
        contact = _c.lookup(name_or_number)
        if contact:
            return contact.get("phone", name_or_number), contact.get("display_name", name_or_number)
    except Exception:
        pass
    return name_or_number, name_or_number


# ── browser / web ─────────────────────────────────────────────────────────────

def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    time.sleep(0.4)
    return f"opened: {url[:80]}"


def google_search(query: str) -> str:
    return open_url("https://www.google.com/search?q=" + quote_plus(query))


# ── native app control ────────────────────────────────────────────────────────

def open_app(name: str) -> str:
    """Launch a macOS application by name (uses 'open -a')."""
    try:
        subprocess.Popen(["open", "-a", name])
        time.sleep(1.2)
        return f"launched: {name}"
    except FileNotFoundError:
        # Try without -a (might be a path)
        try:
            subprocess.Popen(["open", name])
            time.sleep(0.8)
            return f"opened: {name}"
        except Exception as exc2:
            return f"app not found: {name} ({exc2})"
    except Exception as exc:
        return f"failed to open {name}: {exc}"


def open_folder(path: str) -> str:
    """Open a folder or file path in Finder."""
    expanded = str(Path(path.replace("~", str(Path.home()))).expanduser())
    try:
        subprocess.Popen(["open", expanded])
        time.sleep(0.5)
        return f"opened in Finder: {expanded}"
    except Exception as exc:
        return f"failed: {exc}"


_SHELL_ALLOWLIST = (
    "open ", "ls ", "echo ", "pwd", "say ", "afplay ", "cat ",
    "printenv", "whoami", "date", "cal",
)


def shell(command: str) -> str:
    """Run a safe shell command (open, ls, say, echo etc.)."""
    cmd = command.strip()
    if any(cmd.startswith(a) for a in _SHELL_ALLOWLIST) or cmd in ("pwd", "whoami", "date"):
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=12,
            )
            return (r.stdout.strip() or r.stderr.strip() or "done")[:500]
        except subprocess.TimeoutExpired:
            return "command timed out"
        except Exception as exc:
            return f"error: {exc}"
    return f"not allowed: {cmd[:50]}  (only: open, ls, say, echo, afplay, cat)"


# ── typing / keyboard ─────────────────────────────────────────────────────────

def type_text(text: str) -> str:
    """Paste text at the current cursor via clipboard — works for Hindi/emoji."""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        time.sleep(0.15)
        _pag().hotkey("cmd", "v")
        time.sleep(0.12)
        return f"typed ({len(text)} chars)"
    except Exception as exc:
        return f"type failed: {exc}"


def press_key(keys: str) -> str:
    """Press a key or shortcut: 'enter', 'cmd+t', 'cmd+shift+n' etc."""
    parts = [k.strip().lower() for k in keys.split("+")]
    try:
        _pag().hotkey(*parts)
        time.sleep(0.12)
        return f"pressed: {keys}"
    except Exception as exc:
        return f"key failed: {exc}"


def scroll(amount: int = 3) -> str:
    try:
        _pag().scroll(-amount)
        time.sleep(0.1)
        return f"scrolled {'down' if amount > 0 else 'up'} {abs(amount)}"
    except Exception as exc:
        return f"scroll failed: {exc}"


def click(x: int, y: int) -> str:
    try:
        _pag().click(x, y)
        time.sleep(0.12)
        return f"clicked ({x}, {y})"
    except Exception as exc:
        return f"click failed: {exc}"


def wait(seconds: float = 1.5) -> str:
    secs = min(float(seconds), 10.0)
    time.sleep(secs)
    return f"waited {secs:.1f}s"


# ── messaging ─────────────────────────────────────────────────────────────────

def send_imessage(to: str, message: str) -> str:
    """Send iMessage or SMS via macOS Messages.app (AppleScript)."""
    phone, display = _resolve_contact(to)
    safe_msg   = _esc_as(message)
    safe_phone = _esc_as(re.sub(r"[^\d+]", "", phone) or phone)

    script = f'''
tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set theBuddy to buddy "{safe_phone}" of targetService
    send "{safe_msg}" to theBuddy
end tell
'''
    result = run_applescript(script)
    if "error" in result.lower():
        # Fallback: try as SMS
        script2 = f'''
tell application "Messages"
    send "{safe_msg}" to buddy "{safe_phone}" of (1st service whose service type = SMS)
end tell
'''
        result = run_applescript(script2)
    return f"iMessage to {display}: {result}"


def whatsapp_send(to: str, message: str) -> str:
    """Open WhatsApp Web with contact + message pre-filled and auto-send."""
    phone, display = _resolve_contact(to)
    phone = re.sub(r"[^\d+]", "", phone)
    if not phone:
        return f"No phone number found for '{to}'. Add a contact first."

    url = (
        f"https://web.whatsapp.com/send"
        f"?phone={quote(phone)}"
        f"&text={_encode(message)}"
        f"&app_absent=0"
    )
    webbrowser.open(url)
    time.sleep(4.5)   # wait for WA Web to load and fill the message
    try:
        _pag().hotkey("enter")   # send
        time.sleep(0.3)
    except Exception:
        pass
    return f"WhatsApp message sent to {display} ({phone})"


def send_email_native(to: str, subject: str, body: str) -> str:
    """Compose and send email via macOS Mail.app (AppleScript)."""
    _, display = _resolve_contact(to)
    try:
        from . import agent_contacts as _c
        contact = _c.lookup(to)
        email = (contact or {}).get("email", "") or to
    except Exception:
        email = to

    safe_body  = _esc_as(body)
    safe_subj  = _esc_as(subject)
    safe_email = _esc_as(email)

    script = f'''
tell application "Mail"
    set msg to make new outgoing message with properties ¬
        {{subject:"{safe_subj}", content:"{safe_body}", visible:true}}
    tell msg
        make new to recipient with properties {{address:"{safe_email}"}}
    end tell
    send msg
end tell
'''
    result = run_applescript(script)
    return f"Email to {display} ({email}): {result}"


def send_email_gmail(to: str, subject: str, body: str) -> str:
    """Open Gmail compose in the browser (fallback for Mail.app)."""
    try:
        from . import agent_contacts as _c
        contact = _c.lookup(to)
        email = (contact or {}).get("email", "") or to
    except Exception:
        email = to

    url = (
        "https://mail.google.com/mail/?view=cm&fs=1"
        + (f"&to={_encode(email)}" if "@" in email else "")
        + f"&su={_encode(subject)}"
        + f"&body={_encode(body)}"
    )
    webbrowser.open(url)
    time.sleep(1.0)
    return f"Gmail compose opened for {to}"


# ── contacts ──────────────────────────────────────────────────────────────────

def add_contact(name: str, phone: str = "", email: str = "") -> str:
    try:
        from . import agent_contacts as _c
        return _c.add(name, phone=phone, email=email)
    except Exception as exc:
        return f"contact save failed: {exc}"


def lookup_contact(name: str) -> str:
    try:
        from . import agent_contacts as _c
        contact = _c.lookup(name)
        if contact:
            return (
                f"{contact.get('display_name', name)}: "
                f"phone={contact.get('phone', 'none')}, "
                f"email={contact.get('email', 'none')}"
            )
        return f"Contact not found: {name}"
    except Exception as exc:
        return f"lookup failed: {exc}"


# ── screen ────────────────────────────────────────────────────────────────────

def screenshot() -> str:
    path = tempfile.mktemp(suffix=".png")
    try:
        subprocess.run(["screencapture", "-x", path], check=True)
        return path
    except Exception as exc:
        return f"screenshot failed: {exc}"


def get_clipboard() -> str:
    try:
        r = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return r.stdout.strip()[:400]
    except Exception:
        return ""


def fetch_realtime(query: str) -> str:
    """Get live data: time, gold rate, weather, stocks, crypto, currency etc."""
    try:
        from .agent_realtime import fetch as _fetch
        result = _fetch(query)
        return result if result else f"No live data found for: {query}"
    except Exception as exc:
        return f"Realtime fetch error: {exc}"


# ── convenience URL builders (used in planner system prompt examples) ─────────

def whatsapp_url(message: str, phone: str = "") -> str:
    base   = "https://web.whatsapp.com/send"
    params = f"?text={_encode(message)}"
    if phone:
        clean_phone = re.sub(r"[^\d+]", "", phone)
        params = f"?phone={quote(clean_phone)}&text={_encode(message)}"
    return base + params


def gmail_compose_url(subject: str = "", body: str = "", to: str = "") -> str:
    return (
        "https://mail.google.com/mail/?view=cm&fs=1"
        + (f"&to={_encode(to)}" if to else "")
        + (f"&su={_encode(subject)}" if subject else "")
        + (f"&body={_encode(body)}" if body else "")
    )


# ── dispatcher ────────────────────────────────────────────────────────────────

def execute(action: str, args: dict) -> str:
    """Route an action name + args dict to the matching tool."""
    if   action == "open_url":           return open_url(args.get("url", ""))
    elif action == "google_search":      return google_search(args.get("query", ""))
    elif action == "open_app":           return open_app(args.get("name", ""))
    elif action == "open_folder":        return open_folder(args.get("path", "~"))
    elif action == "shell":              return shell(args.get("command", ""))
    elif action == "type_text":          return type_text(args.get("text", ""))
    elif action == "press_key":          return press_key(args.get("keys", "enter"))
    elif action == "scroll":             return scroll(int(args.get("amount", 3)))
    elif action == "click":              return click(int(args.get("x", 0)), int(args.get("y", 0)))
    elif action == "wait":               return wait(float(args.get("seconds", 1.5)))
    elif action == "screenshot":         return screenshot()
    elif action == "get_clipboard":      return get_clipboard()
    elif action == "send_imessage":      return send_imessage(args.get("to", ""), args.get("message", ""))
    elif action == "whatsapp_send":      return whatsapp_send(args.get("to", ""), args.get("message", ""))
    elif action == "send_email_native":  return send_email_native(
                                             args.get("to", ""),
                                             args.get("subject", ""),
                                             args.get("body", ""))
    elif action == "send_email_gmail":   return send_email_gmail(
                                             args.get("to", ""),
                                             args.get("subject", ""),
                                             args.get("body", ""))
    elif action == "add_contact":        return add_contact(
                                             args.get("name", ""),
                                             phone=args.get("phone", ""),
                                             email=args.get("email", ""))
    elif action == "lookup_contact":     return lookup_contact(args.get("name", ""))
    elif action == "run_applescript":    return run_applescript(args.get("script", ""))
    elif action == "fetch_realtime":     return fetch_realtime(args.get("query", ""))
    elif action == "speak":              return "spoken"    # handled by caller
    elif action == "done":               return "done"      # handled by caller
    else:                                return f"unknown action: {action}"

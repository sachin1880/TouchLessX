"""Simple contact store — name → {phone, email}.

Saved to data/contacts.json so contacts persist across sessions.
The agent reads this automatically when you say "send to mom" etc.

Usage (from code):
    from .agent_contacts import lookup, add, list_all
    contact = lookup("mom")   # {"phone": "+91...", "email": "..."}
    add("mom", phone="+919876543210")
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_FILE = Path(__file__).parent.parent / "data" / "contacts.json"


# ── I/O ───────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(contacts: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(
        json.dumps(contacts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── public API ────────────────────────────────────────────────────────────────

def lookup(name: str) -> dict | None:
    """Return contact dict {phone, email, display_name} or None.

    Matches case-insensitively and handles common Hindi/English name variations
    ('mummy' ≈ 'mom', 'papa' ≈ 'dad', etc.).
    """
    contacts = _load()
    key = name.lower().strip()

    # Alias table — extend as needed
    _ALIASES: dict[str, list[str]] = {
        "mom":    ["mummy", "maa", "mother", "mama", "ammi"],
        "dad":    ["papa", "father", "baba", "pitaji", "abba"],
        "brother":["bhai", "bro"],
        "sister": ["didi", "sis", "behan"],
        "wife":   ["patni", "biwi"],
        "husband":["pati"],
    }
    # Reverse-map: "mummy" → "mom"
    _REV: dict[str, str] = {alias: k for k, v in _ALIASES.items() for alias in v}
    key = _REV.get(key, key)   # normalise alias to canonical

    # Exact match
    if key in contacts:
        return contacts[key]

    # Partial / fuzzy: any stored key that contains the query or vice versa
    for stored_key, val in contacts.items():
        if key in stored_key or stored_key in key:
            return val

    return None


def add(name: str, phone: str = "", email: str = "") -> str:
    """Save or update a contact.  Returns a confirmation string."""
    contacts = _load()
    key = name.lower().strip()
    # Normalise phone: keep + and digits only
    phone = re.sub(r"[^\d+]", "", phone)
    contacts[key] = {
        "display_name": name.strip(),
        "phone":        phone,
        "email":        email.strip(),
    }
    _save(contacts)
    parts = []
    if phone:
        parts.append(f"phone {phone}")
    if email:
        parts.append(f"email {email}")
    return f"Contact saved: {name.strip()} ({', '.join(parts) or 'no details yet'})"


def remove(name: str) -> str:
    contacts = _load()
    key = name.lower().strip()
    if key in contacts:
        del contacts[key]
        _save(contacts)
        return f"Removed contact: {name}"
    return f"Contact not found: {name}"


def list_all() -> list[dict]:
    """Return all contacts as a list for display."""
    return [
        {"name": v.get("display_name", k), "phone": v.get("phone", ""), "email": v.get("email", "")}
        for k, v in _load().items()
    ]


def summary() -> str:
    """Short one-line summary of all saved contacts for the AI prompt."""
    contacts = _load()
    if not contacts:
        return "No contacts saved yet."
    parts = []
    for k, v in contacts.items():
        phone = v.get("phone", "")
        email = v.get("email", "")
        info  = phone or email or "no details"
        parts.append(f"{v.get('display_name', k)} ({info})")
    return "Contacts: " + ", ".join(parts)

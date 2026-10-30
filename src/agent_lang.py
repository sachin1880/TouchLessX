"""Language detection and multilingual utilities for the Jarvis agent.

Supports Hindi (Devanagari), English, and Hinglish (Hindi in Roman script).
No external dependencies required for detection.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

LangCode = Literal["auto", "english", "hindi", "hinglish"]

# ── detection patterns ─────────────────────────────────────────────────────────

# Any Devanagari character → Hindi
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Common Hindi words transliterated to Roman script → Hinglish
_HINDI_ROMAN = re.compile(
    r"\b(kya|hai|hain|mein|aur|nahi|kar|mere|aap|bhi|ek|se|ko|ki|ka|yeh|woh|"
    r"toh|par|karo|hua|tha|hoga|sab|bilkul|zaroor|kuch|bahut|achha|theek|samajh|"
    r"batao|dekho|chalao|kholo|band|likho|dhundo|milao|samjhao|bolo|sunao|"
    r"karta|karti|karein|karna|lena|dena|jana|aana|tera|mera|hamara|unka|"
    r"kaisa|kyun|kahan|kab|kitna|jo|jab|tab|phir|matlab|thodi|zyada|"
    r"pehle|baad|abhi|jaldi|dhire|seedha|ulta|upar|neeche)\b",
    re.IGNORECASE,
)

# English-only technical/code indicators
_TECH_ENGLISH = re.compile(
    r"\b(def |class |import |function|return|const |let |var |if |else |for |while |"
    r"python|javascript|react|api|http|json|sql|git|npm|pip)\b",
    re.IGNORECASE,
)

# ── preference storage ─────────────────────────────────────────────────────────

_PREF_FILE = Path(__file__).parent.parent / "data" / "lang_pref.json"


def load_preference() -> LangCode:
    """Return saved language preference.

    Default is "hinglish" — understands both Hindi and English commands
    and replies naturally in whichever mix the user speaks.
    Change from the language selector in the AI overlay header.
    """
    try:
        return json.loads(_PREF_FILE.read_text()).get("lang", "hinglish")  # type: ignore[return-value]
    except Exception:
        return "hinglish"


def save_preference(lang: LangCode) -> None:
    """Persist language preference to disk."""
    try:
        _PREF_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PREF_FILE.write_text(json.dumps({"lang": lang}))
    except Exception:
        pass


# ── detection ─────────────────────────────────────────────────────────────────

def detect_language(text: str) -> LangCode:
    """Detect the dominant language of *text*."""
    if not text.strip():
        return "english"

    # Devanagari script → pure Hindi
    if _DEVANAGARI.search(text):
        return "hindi"

    # Count Hindi Roman-script markers
    words = text.split()
    hindi_hits = len(_HINDI_ROMAN.findall(text))
    ratio = hindi_hits / max(1, len(words))

    if ratio > 0.20 or hindi_hits >= 3:
        return "hinglish"

    return "english"


def resolve_language(text: str, preference: LangCode) -> LangCode:
    """Resolve effective language given *preference* and input *text*."""
    if preference == "auto":
        return detect_language(text)
    return preference


# ── system-prompt instructions ─────────────────────────────────────────────────

def get_language_instruction(lang: LangCode) -> str:
    """Return the language instruction to inject into the AI system prompt."""
    if lang == "hindi":
        return (
            "Always respond in Hindi using Devanagari script. "
            "Keep all code, filenames, terminal commands, API names, and "
            "technical keywords in English exactly as they appear."
        )
    if lang == "hinglish":
        return (
            "Respond in Hinglish — a natural mix of conversational Hindi and English, "
            "written in Roman script (no Devanagari). Keep all code, filenames, "
            "commands, and technical terms in English. "
            'Example style: "Main aapke project ki files analyze kar raha hoon."'
        )
    return "Respond in clear, professional English."


# ── display helpers ────────────────────────────────────────────────────────────

LANG_LABELS: dict[LangCode, str] = {
    "auto":     "AUTO",
    "english":  "EN",
    "hindi":    "हि",
    "hinglish": "MIX",
}

LANG_DISPLAY: dict[LangCode, str] = {
    "auto":     "Auto Detect",
    "english":  "English",
    "hindi":    "हिंदी",
    "hinglish": "Hinglish",
}

LANG_ORDER: list[LangCode] = ["auto", "english", "hindi", "hinglish"]

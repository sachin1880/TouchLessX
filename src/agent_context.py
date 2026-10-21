"""Context classifier: maps a voice command string to an agent mode.

Returned mode strings match the keys in agent_theme.THEMES.
Returns '' for general conversation (no mode override).
"""
from __future__ import annotations

import re

# (regex pattern, mode) — checked top-to-bottom, first match wins.
_RULES: list[tuple[str, str]] = [
    # Phone / communication
    (
        r'\b(call|phone|message|text|contact|sms|ring|dial|send email|'
        r'whatsapp|telegram|signal)\b',
        "phone",
    ),
    # Calendar / meeting
    (
        r'\b(calendar|meeting|schedule|appointment|agenda|event|'
        r'remind|reminder|today|tomorrow|next week|deadline|due date|standup)\b',
        "meeting",
    ),
    # Focus mode
    (
        r'\b(focus mode|focus|quiet|minimal|do not disturb|dnd|concentrate|'
        r'block notifications)\b',
        "focus",
    ),
    # Coding / dev tasks
    (
        r'\b(code|coding|program|programming|python|javascript|typescript|'
        r'function|class|method|debug|git|commit|push|pull|branch|merge|'
        r'file|folder|directory|run|terminal|execute|script|test|build|'
        r'compile|deploy|variable|import|module|package|pip|npm|yarn|'
        r'error|bug|fix|refactor|lint|format|syntax|review my code|'
        r'explain this code|write a function|write a class|write a script)\b',
        "coding",
    ),
    # Research / information gathering
    (
        r'\b(search|find|look up|lookup|research|explain|what is|what are|'
        r'how does|how do|who is|who are|wikipedia|information|definition|'
        r'meaning|tell me about|describe|show me|summarize|summarise|'
        r'latest news|news about)\b',
        "research",
    ),
]

_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat, re.IGNORECASE), mode) for pat, mode in _RULES
]


def classify(command: str) -> str:
    """Return the best matching mode for *command*, or '' for general chat."""
    for pattern, mode in _COMPILED:
        if pattern.search(command):
            return mode
    return ""

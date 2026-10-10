"""Clipboard history: the last few pieces of text that were copied.

Kept in memory only - never written to disk - and emptied when the app closes or the
user clears it. It exists so an earlier copy can be pasted again by gesture or click.
"""
from __future__ import annotations


class ClipboardHistory:
    def __init__(self, max_items: int = 10, max_chars: int = 20_000) -> None:
        self.max_items = max_items
        self.max_chars = max_chars
        self.items: list[str] = []  # newest first

    def add(self, text) -> bool:
        """Notes the clipboard's current text. Returns True if the history changed."""
        if not isinstance(text, str) or not text.strip() or len(text) > self.max_chars:
            return False  # empty, not text, or too large to be worth holding in memory
        if self.items and self.items[0] == text:
            return False
        if text in self.items:
            self.items.remove(text)  # copied again: move it to the front instead of duplicating
        self.items.insert(0, text)
        del self.items[self.max_items:]
        return True

    def previous(self) -> str | None:
        """The text copied before the current one."""
        return self.items[1] if len(self.items) > 1 else None

    def clear(self) -> None:
        self.items = []

    @staticmethod
    def preview(text: str, length: int = 90) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= length else flat[: length - 1] + "…"

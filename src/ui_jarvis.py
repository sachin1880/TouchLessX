"""JARVIS Developer Assistant page — embedded inside TouchlessX.

Provides:
  • Open a project folder and index it
  • Chat interface to ask code / git questions (voice or typed)
  • Live text search across the indexed project
  • Git status at a glance
  • Companion server launch button
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .ui_pages import Page
from .ui_widgets import (
    ACCENT, AMBER, BG, BORDER, GREEN, PANEL, PANEL_ALT, RED, TEXT, TEXT_DIM, TRACK,
    button, card, label,
)

_CTK_RED = "#f85149"
_CTK_GREEN = "#3fb950"
_CTK_BLUE = "#58a6ff"
_CTK_AMBER = "#d29922"


class JarvisDevPage(Page):
    title = "JARVIS Developer"
    subtitle = "AI code assistant · project indexer · git · companion server"

    def build(self) -> None:
        bridge = getattr(self.app, "jarvis", None)
        self._bridge = bridge

        # ── Project section ──────────────────────────────────────────
        proj_sec = self.section("Project", "Open a project folder to let JARVIS read and understand your code.")

        status_row = ctk.CTkFrame(proj_sec, fg_color="transparent")
        status_row.pack(fill="x", padx=18, pady=(4, 0))
        self._status_lbl = label(status_row, self._status_text(), 12, color=TEXT_DIM)
        self._status_lbl.pack(side="left")

        btn_row = ctk.CTkFrame(proj_sec, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=(8, 0))
        button(btn_row, "Open project…", self._open_project).pack(side="left", padx=(0, 8))
        self._reindex_btn = button(btn_row, "Re-index", self._reindex,
                                   fg_color=PANEL_ALT, text_color=TEXT)
        self._reindex_btn.pack(side="left")

        self.end(proj_sec)

        # ── Chat section ─────────────────────────────────────────────
        chat_sec = self.section("Chat", "Ask anything about your code. Voice commands starting with 'computer' also appear here.")
        self._chat_box = ctk.CTkTextbox(chat_sec, height=260, fg_color=BG,
                                        text_color=TEXT, font=("Courier", 12),
                                        state="disabled", wrap="word")
        self._chat_box.pack(fill="x", padx=18, pady=(4, 0))
        inp_row = ctk.CTkFrame(chat_sec, fg_color="transparent")
        inp_row.pack(fill="x", padx=18, pady=(6, 0))
        self._input = ctk.CTkEntry(inp_row, placeholder_text="Ask JARVIS anything…",
                                   fg_color=PANEL_ALT, text_color=TEXT, border_color=BORDER)
        self._input.pack(side="left", fill="x", expand=True)
        self._input.bind("<Return>", lambda _e: self._send())
        button(inp_row, "Ask", self._send).pack(side="left", padx=(6, 0))
        self.end(chat_sec)

        # ── Search section ───────────────────────────────────────────
        srch_sec = self.section("Code Search", "Search text or symbols across the indexed project.")
        s_row = ctk.CTkFrame(srch_sec, fg_color="transparent")
        s_row.pack(fill="x", padx=18, pady=(4, 0))
        self._search_entry = ctk.CTkEntry(s_row, placeholder_text="search text or symbol…",
                                          fg_color=PANEL_ALT, text_color=TEXT, border_color=BORDER)
        self._search_entry.pack(side="left", fill="x", expand=True)
        self._search_entry.bind("<Return>", lambda _e: self._search())
        button(s_row, "Search", self._search).pack(side="left", padx=(6, 0))
        self._search_results = ctk.CTkTextbox(srch_sec, height=140, fg_color=BG,
                                              text_color=TEXT_DIM, font=("Courier", 11),
                                              state="disabled", wrap="none")
        self._search_results.pack(fill="x", padx=18, pady=(6, 0))
        self.end(srch_sec)

        # ── Git section ──────────────────────────────────────────────
        git_sec = self.section("Git Status")
        self._git_lbl = label(git_sec, "—", 12, color=TEXT_DIM)
        self._git_lbl.pack(anchor="w", padx=18, pady=(4, 0))
        button(git_sec, "Refresh", self._refresh_git,
               fg_color=PANEL_ALT, text_color=TEXT).pack(anchor="w", padx=18, pady=(6, 0))
        self.end(git_sec)

        # ── Companion server section ──────────────────────────────────
        srv_sec = self.section("Companion Server", "Start the HTTPS server so the JARVIS phone app can connect.")
        btn_row2 = ctk.CTkFrame(srv_sec, fg_color="transparent")
        btn_row2.pack(fill="x", padx=18, pady=(4, 0))
        button(btn_row2, "Start Server (port 7474)", self._start_server).pack(side="left", padx=(0, 8))
        button(btn_row2, "Show Pairing Code", self._pairing_code,
               fg_color=PANEL_ALT, text_color=TEXT).pack(side="left")
        self.end(srv_sec)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self._refresh_status()
        self._refresh_git()

    def _status_text(self) -> str:
        b = self._bridge
        if b is None:
            return "JARVIS not available (check JARVIS project path)"
        if b.get_error():
            return f"Error: {b.get_error()}"
        if b.is_ready():
            return f"Indexed: {b.get_project()}  ({b.file_count} files)"
        if b.get_project():
            return f"Indexing {b.get_project()}…"
        return "No project loaded"

    def _refresh_status(self) -> None:
        if hasattr(self, "_status_lbl"):
            self._status_lbl.configure(text=self._status_text())

    # ------------------------------------------------------------------
    def _open_project(self) -> None:
        path = filedialog.askdirectory(title="Open project folder")
        if not path or self._bridge is None:
            return
        self._append_chat("system", f"Indexing {path}…")
        self._refresh_status()
        self._bridge.index_project(path, done_cb=lambda: self.after(0, self._on_indexed))

    def _reindex(self) -> None:
        if self._bridge is None or not self._bridge.get_project():
            return
        path = self._bridge.get_project()
        self._append_chat("system", f"Re-indexing {path}…")
        self._bridge.index_project(path, done_cb=lambda: self.after(0, self._on_indexed))

    def _on_indexed(self) -> None:
        self._refresh_status()
        if self._bridge and self._bridge.is_ready():
            self._append_chat("jarvis", f"Ready — {self._bridge.file_count} files indexed in {Path(self._bridge.get_project()).name}")
        elif self._bridge:
            self._append_chat("error", self._bridge.get_error())

    # ------------------------------------------------------------------
    def _send(self) -> None:
        text = self._input.get().strip()
        if not text:
            return
        self._input.delete(0, "end")
        self.ask_jarvis(text)

    def ask_jarvis(self, text: str) -> None:
        """Called from voice handler or the typed input field."""
        if not self._bridge or not self._bridge.is_ready():
            self._append_chat("error", "No project loaded. Open a project first.")
            return
        self._append_chat("you", text)
        self._append_chat("jarvis", "…thinking…")

        def _run():
            # Replace the placeholder once the first token arrives
            first = [True]
            parts: list[str] = []

            def _token(t: str) -> None:
                parts.append(t)
                self.after(0, lambda tok="".join(parts): self._update_last_chat("jarvis", tok))

            answer = self._bridge.ask(text, on_token=_token)
            self.after(0, lambda a=answer: self._update_last_chat("jarvis", a))

        threading.Thread(target=_run, daemon=True, name="jarvis-ask").start()

    # ------------------------------------------------------------------
    def _search(self) -> None:
        q = self._search_entry.get().strip()
        if not q or not self._bridge:
            return
        results = self._bridge.search_text(q) or self._bridge.search_symbol(q)
        self._search_results.configure(state="normal")
        self._search_results.delete("1.0", "end")
        if results:
            for r in results[:30]:
                if "content" in r:
                    self._search_results.insert("end", f"{r['path']}:{r['line']}  {r['content']}\n")
                else:
                    self._search_results.insert("end", f"{r['path']}:{r['line']}  {r['kind']} {r['name']}\n")
        else:
            self._search_results.insert("end", "No results")
        self._search_results.configure(state="disabled")

    # ------------------------------------------------------------------
    def _refresh_git(self) -> None:
        if not self._bridge:
            return
        status = self._bridge.git_status()
        if hasattr(self, "_git_lbl"):
            self._git_lbl.configure(text=status)

    # ------------------------------------------------------------------
    def _start_server(self) -> None:
        if self._bridge:
            self._bridge.start_companion_server()
            self._append_chat("system", "Companion server starting on port 7474 (HTTPS). Use Phone menu to get the pairing code.")

    def _pairing_code(self) -> None:
        try:
            import sys
            from pathlib import Path
            _JARVIS_ROOT = Path(__file__).parent.parent.parent / "JARVIS"
            if str(_JARVIS_ROOT) not in sys.path:
                sys.path.insert(0, str(_JARVIS_ROOT))
            from jarvis.server.auth import generate_pairing_code
            code, _ = generate_pairing_code()
            self._append_chat("system", f"Pairing code (expires in 60 s):  {code}")
        except Exception as e:
            self._append_chat("error", str(e))

    # ------------------------------------------------------------------
    def _append_chat(self, role: str, text: str) -> None:
        role_prefix = {"you": "YOU", "jarvis": "JARVIS", "system": "INFO", "error": "ERROR"}
        colors = {"you": ACCENT, "jarvis": _CTK_GREEN, "system": _CTK_AMBER, "error": _CTK_RED}
        prefix = role_prefix.get(role, role.upper())
        line = f"[{prefix}] {text}\n"
        self._chat_box.configure(state="normal")
        self._chat_box.insert("end", line)
        self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

    def _update_last_chat(self, role: str, text: str) -> None:
        """Replace the last line that starts with [ROLE] with new text."""
        prefix = f"[{role.upper()}] "
        self._chat_box.configure(state="normal")
        content = self._chat_box.get("1.0", "end")
        lines = content.split("\n")
        # find last matching line from the end
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith(prefix):
                lines[i] = f"{prefix}{text}"
                break
        self._chat_box.delete("1.0", "end")
        self._chat_box.insert("1.0", "\n".join(lines))
        self._chat_box.see("end")
        self._chat_box.configure(state="disabled")

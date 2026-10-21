"""Bridge between TouchlessX and the JARVIS developer agent.

Imports from the JARVIS project (sibling directory) and exposes a simple
synchronous API that TouchlessX's voice/assistant flow can call.

Usage:
    bridge = JarvisBridge()
    bridge.set_project("/path/to/project")
    answer = bridge.ask("what does login.py do?")   # blocks; run on a thread
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

# Add the JARVIS project to sys.path so we can import from it.
_JARVIS_ROOT = Path(__file__).parent.parent.parent / "JARVIS"
if _JARVIS_ROOT.exists() and str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))

# Dev-query keywords — if a voice request matches any of these we route to JARVIS
_DEV_PATTERNS = re.compile(
    r"\b(explain|describe|review|audit|what does|find function|find class|"
    r"search for|grep|git status|git diff|git log|where is|show me the code|"
    r"read file|open file|list files|run tests|apply patch|create file|"
    r"what changed|how does .+ work|what is .+ for|endpoint|api route|"
    r"bug|error|fix|refactor)\b",
    re.I,
)


def is_dev_query(text: str) -> bool:
    """Return True if the query sounds like a developer/code question."""
    return bool(_DEV_PATTERNS.search(text))


class JarvisBridge:
    """Thread-safe wrapper around a JARVIS AgentSession."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._project: str = ""
        self._ready = False
        self._error: str = ""

    # ------------------------------------------------------------------
    def set_project(self, path: str) -> None:
        """Index *path* and create a fresh agent session. Call on a thread."""
        with self._lock:
            self._ready = False
            self._error = ""
            self._project = path
        try:
            from jarvis.config import load as load_cfg, load_from_env
            from jarvis.llm import make_llm, LLMError
            from jarvis.project.indexer import build_index
            from jarvis.agent.executor import AgentSession

            cfg = load_from_env(load_cfg())
            try:
                llm = make_llm(cfg)
            except LLMError as e:
                with self._lock:
                    self._error = str(e)
                return

            index = build_index(path, cfg)
            session = AgentSession(path, index, llm)
            with self._lock:
                self._session = session
                self._ready = True
        except ImportError as e:
            with self._lock:
                self._error = f"JARVIS not installed: {e}"
        except Exception as e:
            with self._lock:
                self._error = str(e)

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def get_error(self) -> str:
        with self._lock:
            return self._error

    def get_project(self) -> str:
        with self._lock:
            return self._project

    # ------------------------------------------------------------------
    def ask(self, text: str,
            on_token: Optional[Callable[[str], None]] = None) -> str:
        """Run the agent and return the full answer.

        If *on_token* is provided it is called for each streamed token
        (on the same thread — the caller is responsible for thread safety
        if it touches UI widgets).
        """
        with self._lock:
            session = self._session
            ready = self._ready

        if not ready or session is None:
            err = self.get_error()
            return err or "JARVIS developer agent is not ready. Open a project first."

        parts: list[str] = []
        try:
            for event in session.ask(text):
                if event.kind == "answer":
                    parts.append(event.data)
                    if on_token:
                        on_token(event.data)
                elif event.kind == "error":
                    return f"[error] {event.data}"
        except Exception as e:
            return f"[error] {e}"

        return "".join(parts) or "(no response)"

    # ------------------------------------------------------------------
    def index_project(self, path: str, done_cb: Optional[Callable[[], None]] = None) -> None:
        """Kick off project indexing on a background thread."""
        def _go():
            self.set_project(path)
            if done_cb:
                done_cb()
        threading.Thread(target=_go, daemon=True, name="jarvis-index").start()

    # ------------------------------------------------------------------
    @property
    def file_count(self) -> int:
        with self._lock:
            if self._session is None:
                return 0
            return len(self._session.executor.index)

    def search_text(self, query: str, limit: int = 20) -> list[dict]:
        with self._lock:
            session = self._session
        if session is None:
            return []
        try:
            from jarvis.project.searcher import text_search
            matches = text_search(
                self._project, session.executor.index, query, limit=limit
            )
            return [{"path": m.path, "line": m.line, "content": m.content} for m in matches]
        except Exception:
            return []

    def search_symbol(self, name: str) -> list[dict]:
        with self._lock:
            session = self._session
        if session is None:
            return []
        try:
            from jarvis.project.searcher import symbol_search
            matches = symbol_search(session.executor.index, name)
            return [{"path": m.path, "line": m.line, "name": m.name, "kind": m.kind} for m in matches]
        except Exception:
            return []

    def git_status(self) -> str:
        if not self._project:
            return "No project loaded"
        try:
            from jarvis.project.git_info import get_status
            st = get_status(self._project)
            return st.summary
        except Exception as e:
            return str(e)

    def start_companion_server(self) -> None:
        """Launch the JARVIS FastAPI companion server in a background process."""
        import subprocess as sp
        script = _JARVIS_ROOT / "scripts" / "start_server.py"
        if not script.exists():
            return
        sp.Popen(
            [sys.executable, str(script)],
            start_new_session=True,
            stdout=sp.DEVNULL, stderr=sp.DEVNULL,
        )

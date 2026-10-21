"""Multilingual text-to-speech for the AI agent overlay.

Priority chain (first available wins):
  1. gTTS + afplay/mpg123  — best Hindi/Hinglish (requires: pip install gtts)
  2. macOS `say -v Lekha`  — Indian-English fallback for Hindi/Hinglish
  3. macOS `say -v Daniel` — English
  4. pyttsx3               — non-macOS fallback

Run in a daemon thread; callbacks fire on that same thread.
Install gTTS for full Hindi: pip install gtts
"""
from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import threading
from typing import Callable, Optional

# macOS voice names per language
_MAC_VOICES: dict[str, str] = {
    "english":  "Daniel",   # clear British English
    "hindi":    "Lekha",    # Indian English — best built-in for Hindi accent
    "hinglish": "Lekha",
    "auto":     "Daniel",
}

# Language codes for gTTS
_GTTS_LANG: dict[str, str] = {
    "english":  "en",
    "hindi":    "hi",
    "hinglish": "hi",   # Hindi engine handles Hinglish well
    "auto":     "en",
}


def _gtts_available() -> bool:
    try:
        import importlib
        return importlib.util.find_spec("gtts") is not None
    except Exception:
        return False


def _play_file(path: str) -> None:
    """Play an audio file cross-platform."""
    if platform.system() == "Darwin":
        subprocess.run(["afplay", path], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    elif platform.system() == "Linux":
        # try mpg123, else aplay
        for player in ("mpg123", "aplay"):
            if subprocess.run(["which", player], capture_output=True).returncode == 0:
                subprocess.run([player, path], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                break
    else:
        # Windows — playsound fallback
        try:
            import playsound
            playsound.playsound(path)
        except Exception:
            pass


class AgentTTS:
    """Thread-safe multilingual TTS engine.

    Call speak(text, lang=…); stop() aborts a running utterance.
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._gtts  = _gtts_available()

    # ──────────────────────────────────────────────────────── public API

    def speak(self, text: str, lang: str = "english",
              on_start: Optional[Callable[[], None]] = None,
              on_end:   Optional[Callable[[], None]] = None) -> None:
        """Speak *text* in *lang* (english | hindi | hinglish | auto)."""
        self.stop()
        threading.Thread(
            target=self._run,
            args=(text[:900], lang, on_start, on_end),
            daemon=True, name="agent-tts",
        ).start()

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None

    def is_speaking(self) -> bool:
        with self._lock:
            return self._proc is not None

    # ──────────────────────────────────────────────────────── internals

    def _run(self, text: str, lang: str,
             on_start: Optional[Callable[[], None]],
             on_end:   Optional[Callable[[], None]]) -> None:
        if on_start:
            on_start()
        try:
            if self._gtts and lang in ("hindi", "hinglish"):
                self._speak_gtts(text, lang)
            elif platform.system() == "Darwin":
                self._speak_mac(text, lang)
            else:
                self._speak_pyttsx(text)
        finally:
            with self._lock:
                self._proc = None
            if on_end:
                on_end()

    def _speak_mac(self, text: str, lang: str) -> None:
        voice = _MAC_VOICES.get(lang, "Daniel")
        cmd = ["say", "-v", voice, text]
        with self._lock:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        self._proc.wait()

    def _speak_gtts(self, text: str, lang: str) -> None:
        try:
            from gtts import gTTS  # type: ignore[import]

            gtts_lang = _GTTS_LANG.get(lang, "en")
            tts = gTTS(text=text, lang=gtts_lang, slow=False)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp = f.name
            tts.save(tmp)
            try:
                cmd = ["afplay", tmp] if platform.system() == "Darwin" else ["mpg123", tmp]
                with self._lock:
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                self._proc.wait()
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        except Exception:
            # gTTS failed (network issue etc.) — fall back to native
            self._speak_mac(text, lang)

    def _speak_pyttsx(self, text: str) -> None:
        try:
            import pyttsx3  # type: ignore[import]
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass

"""Voice commands: offline speech recognition, a wake word, and a command parser.

Speech is recognised on this computer by Vosk with a small English model; no audio or
text leaves the machine. Recognition is restricted to the words commands are made of,
which is what makes a small model accurate enough. The parser turns recognised text
into one of the app's existing actions - it understands a set of commands and their
natural variations, not arbitrary requests, and it can never run a command line.
"""
from __future__ import annotations

import json
import platform
import queue
import shutil
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
SAMPLE_RATE = 16000
WAKE_WORDS = ("computer", "hey computer", "okay computer")
COMMAND_WINDOW_SECONDS = 6.0  # after the wake word alone, how long the next phrase counts as a command


class VoiceError(RuntimeError):
    """Raised when the speech model or the microphone can't be set up."""


@dataclass(frozen=True)
class VoiceCommand:
    kind: str            # "action", "scroll", "stop_scroll", "open_app", "resume"
    label: str           # what to show the user
    action: str = ""     # for kind "action": an id from action_controller.ACTIONS
    direction: str = ""  # for kind "scroll": up / down / left / right
    speed: str = ""      # for kind "scroll": "", "small", "fast", "slow" or "keep"
    app: str = ""        # for kind "open_app": the application's name as installed


# Words that carry no meaning in a command ("scroll this page down please").
_FILLER = {"please", "the", "this", "that", "a", "an", "my", "can", "you", "could", "would", "to", "now", "just",
           "it", "me", "for", "of", "on", "and", "then", "page", "pages", "one", "current", "hey", "okay"}
_SIMPLE = (  # (words that must all be present, action id, label) - first match wins
    ({"double", "click"}, "double_click", "Double Click"),
    ({"right", "click"}, "right_click", "Right Click"),
    ({"click"}, "left_click", "Click"),
    ({"select", "all"}, "select_all", "Select All"),
    ({"copy"}, "copy", "Copy"),
    ({"paste"}, "paste", "Paste"),
    ({"cut"}, "cut", "Cut"),
    ({"undo"}, "undo", "Undo"),
    ({"redo"}, "redo", "Redo"),
    ({"delete"}, "delete", "Delete"),
    ({"volume", "up"}, "volume_up", "Volume Up"),
    ({"louder"}, "volume_up", "Volume Up"),
    ({"volume", "down"}, "volume_down", "Volume Down"),
    ({"quieter"}, "volume_down", "Volume Down"),
    ({"mute"}, "mute", "Mute"),
    ({"next", "slide"}, "next_slide", "Next Slide"),
    ({"previous", "slide"}, "prev_slide", "Previous Slide"),
    ({"next", "song"}, "next_track", "Next Track"),
    ({"next", "track"}, "next_track", "Next Track"),
    ({"previous", "song"}, "prev_track", "Previous Track"),
    ({"previous", "track"}, "prev_track", "Previous Track"),
    ({"play"}, "play_pause", "Play / Pause"),
    ({"pause", "music"}, "play_pause", "Play / Pause"),
    ({"pause", "video"}, "play_pause", "Play / Pause"),
    ({"zoom", "in"}, "zoom_in", "Zoom In"),
    ({"zoom", "out"}, "zoom_out", "Zoom Out"),
    ({"reload"}, "reload", "Reload Page"),
    ({"refresh"}, "reload", "Reload Page"),
    ({"forward"}, "go_forward", "Go Forward"),
    ({"back"}, "go_back", "Go Back"),
    ({"minimize"}, "minimize_window", "Minimize Window"),
    ({"restore"}, "restore_window", "Restore Window"),
    ({"unlock"}, "confirm", "Unlock"),
    ({"lock"}, "lock_controls", "Lock Controls"),
    ({"pause"}, "play_pause", "Play / Pause"),
)
# Every word a command can contain; recognition is limited to these plus application names.
_COMMAND_WORDS = (
    "computer hey okay please the this that a an my can you could would to now just it me for of on in and then "
    "page pages one current scroll scrolling up down left right slowly slow fast quickly quick faster slower little "
    "bit lot more keep continue continuously top bottom start beginning end go stop halt everything emergency "
    "pause resume control gestures tab tabs next previous new close change switch last open launch window "
    "application app minimize restore click double select all copy paste cut undo redo delete volume louder "
    "quieter mute play music video song track slide zoom reload refresh back forward lock unlock"
).split()


def command_vocabulary(app_names=()) -> list[str]:
    words = set(_COMMAND_WORDS)
    for name in app_names:
        words.update(w for w in name.lower().split() if w.isalpha())
    return sorted(words)


def match_app(spoken: list[str], app_names) -> str | None:
    """The installed application a spoken name refers to: "chrome" finds "Google Chrome"."""
    spoken = [w for w in spoken if w not in ("app", "application")]
    if not spoken:
        return None
    best = None
    for name in app_names:
        words = name.lower().split()
        if words == spoken:
            return name
        if all(w in words for w in spoken) and (best is None or len(name) < len(best)):
            best = name
    return best


def parse_command(text: str, app_names=()) -> VoiceCommand | None:
    """Turns recognised speech (without the wake word) into a command, or None."""
    raw = [w for w in text.lower().replace("[unk]", " ").split() if w]
    if not raw:
        return None
    words = [w for w in raw if w not in _FILLER]
    has = lambda *needed: all(w in words for w in needed)

    verb = next((i for i, w in enumerate(raw[:4]) if w in ("open", "launch") or (w == "start" and i == 0)), None)
    if verb is not None:
        name = match_app([w for w in raw[verb + 1:] if w not in ("the", "a", "my", "please")], app_names)
        if name:
            return VoiceCommand("open_app", f"Open {name}", app=name)
        # not an application ("open a new tab"): fall through to the other commands

    if has("emergency") or has("stop", "everything") or has("pause", "control") or has("pause", "gestures"):
        return VoiceCommand("action", "Emergency Stop", action="emergency_stop")
    if words in (["resume"], ["resume", "control"], ["continue"]):
        return VoiceCommand("resume", "Resume Control")
    if words and words[0] in ("stop", "halt"):
        return VoiceCommand("stop_scroll", "Stop Scrolling")

    if has("tab") or has("tabs"):
        if has("new"):
            return VoiceCommand("action", "New Tab", action="new_tab")
        if has("close"):
            return VoiceCommand("action", "Close Tab", action="close_tab")
        if has("previous") or has("last") or has("back"):
            return VoiceCommand("action", "Previous Tab", action="prev_tab")
        return VoiceCommand("action", "Next Tab", action="next_tab")
    if (has("switch") or has("change") or has("next")) and (has("app") or has("application") or has("window")):
        return VoiceCommand("action", "Switch Application", action="switch_app")
    if has("close", "window"):
        return VoiceCommand("action", "Close Window", action="close_tab")

    scrolling = has("scroll") or has("scrolling")
    if has("top") or has("beginning"):
        if scrolling or has("go"):
            return VoiceCommand("action", "Jump to Top", action="scroll_top")
    if has("bottom") or has("end"):
        if scrolling or has("go"):
            return VoiceCommand("action", "Jump to Bottom", action="scroll_bottom")
    if not scrolling and "page" in raw and (has("up") or has("down")):  # "page down"
        action = "page_up" if has("up") else "page_down"
        return VoiceCommand("action", "Page Up" if has("up") else "Page Down", action=action)
    if scrolling or (has("go") and (has("up") or has("down"))):
        direction = next((d for d in ("up", "down", "left", "right") if d in words), "down")
        if has("slowly") or has("slow") or has("slower"):
            speed = "slow"
        elif has("keep") or has("continuously") or has("continue"):
            speed = "keep"
        elif has("fast") or has("quickly") or has("quick") or has("faster") or has("lot"):
            speed = "fast"
        elif has("little") or has("bit"):
            speed = "small"
        else:
            speed = ""
        note = {"slow": " slowly", "keep": " steadily", "fast": " fast", "small": " a little"}.get(speed, "")
        return VoiceCommand("scroll", f"Scroll {direction.capitalize()}{note}", direction=direction, speed=speed)

    for needed, action, label in _SIMPLE:
        if has(*needed):
            return VoiceCommand("action", label, action=action)
    return None


class WakeGate:
    """Only speech addressed to the app counts: either "computer, scroll down" in one
    breath, or "computer" followed by the command within a few seconds."""

    def __init__(self, wake: str = "computer", window: float = COMMAND_WINDOW_SECONDS) -> None:
        self.wake = wake.split()[-1]  # "hey computer" and "okay computer" hinge on the same word
        self.window = window
        self._armed_until = -1e9

    def armed(self, now: float) -> bool:
        return now < self._armed_until

    def disarm(self) -> None:
        self._armed_until = -1e9

    def feed(self, text: str, now: float) -> str | None:
        """Returns the command part of a recognised phrase, or None if it wasn't for us."""
        words = [w for w in text.lower().split() if w != "[unk]"]
        if self.wake in words:
            rest = words[words.index(self.wake) + 1:]
            if rest:
                self._armed_until = -1e9
                return " ".join(rest)
            self._armed_until = now + self.window
            return None
        if now < self._armed_until and words:
            self._armed_until = -1e9
            return " ".join(words)
        return None


def installed_apps(extra: dict[str, str] | None = None) -> dict[str, str]:
    """name -> path of applications that may be opened by voice: the ones installed in
    the standard places (macOS) plus the ones the user added under System Controls."""
    apps: dict[str, str] = {}
    if platform.system() == "Darwin":
        for folder in ("/Applications", "/System/Applications", "/System/Applications/Utilities",
                       str(Path.home() / "Applications")):
            try:
                for item in sorted(Path(folder).glob("*.app")):
                    apps.setdefault(item.stem, str(item))
            except OSError:
                continue
    apps.update(extra or {})
    return apps


def ensure_voice_model(folder: Path) -> Path:
    """Returns the local speech model directory, downloading it once (about 40 MB)."""
    target = folder / MODEL_NAME
    if (target / "am" / "final.mdl").exists():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / (MODEL_NAME + ".zip.part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, open(archive, "wb") as f:
            shutil.copyfileobj(response, f)
        with zipfile.ZipFile(archive) as bundle:
            root = folder.resolve()
            for member in bundle.namelist():  # refuse anything that would land outside the folder
                if not (root / member).resolve().is_relative_to(root):
                    raise VoiceError("The downloaded speech model archive is not safe to unpack.")
            bundle.extractall(folder)
    except (OSError, zipfile.BadZipFile) as exc:
        raise VoiceError(
            f"Could not download the speech model (one-time, about 40 MB): {exc}\n"
            f"Download {MODEL_URL} and unzip it into {folder}"
        ) from exc
    finally:
        archive.unlink(missing_ok=True)
    if not (target / "am" / "final.mdl").exists():
        raise VoiceError("The speech model did not unpack correctly.")
    return target


class Transcriber:
    """Open-vocabulary speech-to-text (Whisper, running on this computer) for requests
    that are more than a fixed command: a song name, a search, a mail topic. The model
    (about 150 MB) is downloaded on first use and loaded only when first needed."""

    def __init__(self, folder: Path, size: str = "base") -> None:
        self._folder = folder
        self._size = size
        self._model = None
        # Shown to the recogniser as "what was said before": it spells the wake word and
        # the names it is likely to hear the way they should come out.
        self.prompt = "Computer, open YouTube and search for a song. Computer, write a mail. Open Gmail."

    def transcribe(self, pcm16: bytes) -> str:
        import numpy as np

        if self._model is None:
            import os

            # The hub's newer transfer method stalled on this model during development.
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self._size, device="cpu", compute_type="int8", download_root=str(self._folder))
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=1, condition_on_previous_text=False,
            # Spells the wake word and the usual destinations the way they should come out.
            initial_prompt=self.prompt)
        return " ".join(segment.text.strip() for segment in segments).strip()


def strip_wake_word(text: str, wake: str = "computer") -> str:
    """'Computer, open YouTube.' -> 'open YouTube.' (the text is returned whole if the
    wake word isn't in it - it was heard by the other recogniser, that is enough)."""
    import re

    match = re.search(rf"\b{re.escape(wake.split()[-1])}\b[\s,.!:;-]*", text, flags=re.IGNORECASE)
    return (text[match.end():] if match else text).strip()


class VoiceListener(threading.Thread):
    """Listens on the microphone and reports what was asked.

    A small recogniser limited to command words runs all the time: it spots the wake
    word, marks where each phrase ends, and settles plain commands instantly. A phrase
    addressed to the app that is more than a plain command is then transcribed again by
    the open-vocabulary recogniser. Speech without the wake word is never transcribed.

    `on_heard(kind, text)` gets "command" (a plain command, wake word removed), "free"
    (a free request, wake word removed), "wake" (the wake word alone: now waiting for
    the request) or "ignored". `on_state(state, detail)` reports "loading",
    "listening", "thinking", "silent", "error" or "stopped". Both run on this thread.
    """

    def __init__(self, model_folder: Path, vocabulary: list[str], on_heard, on_state, wake: str = "computer",
                 app_names=(), transcriber: Transcriber | None = None, free_words=()) -> None:
        super().__init__(daemon=True)
        self._model_folder = model_folder
        self._vocabulary = vocabulary
        self._on_heard = on_heard
        self._on_state = on_state
        self._gate = WakeGate(wake)
        self._wake = wake
        self._app_names = list(app_names)
        self._transcriber = transcriber
        # Words that signal a free request even when every word was recognised ("open
        # gmail", "search ...", "write ..."): those always get the freer second hearing.
        self._free_words = set(free_words)
        self._stop_event = threading.Event()
        self._new_vocabulary: list[str] | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        self._new_vocabulary = vocabulary

    def handle_phrase(self, text: str, audio: bytes, now: float) -> None:
        """Decides what one finished phrase was: `text` is what the command recogniser
        made of it, `audio` the sound itself in case it needs a second, freer hearing."""
        was_armed = self._gate.armed(now)
        words = text.lower().split()
        # Words outside the command vocabulary come out as "[unk]". "computer [unk] [unk]"
        # therefore is not the wake word alone - something was asked that needs a freer hearing.
        said_more = self._gate.wake in words and "[unk]" in words[words.index(self._gate.wake) + 1:]
        spoken = self._gate.feed(text, now)
        if spoken is None and was_armed and words and self._gate.wake not in words:
            said_more = True  # the awaited request, made entirely of words outside the command vocabulary
        if spoken is None and said_more and self._transcriber is not None:
            self._gate.disarm()  # this phrase was the request itself, not a "yes?"
            spoken = ""
        if spoken is None:
            self._on_heard("wake" if self._gate.armed(now) else "ignored", text)
            return
        plain = ("[unk]" not in text and not (set(spoken.split()) & self._free_words)
                 and parse_command(spoken, self._app_names) is not None)
        if plain or self._transcriber is None:
            self._on_heard("command", spoken.replace("[unk]", "").strip())
            return
        self._on_state("thinking", "")
        try:
            free = self._transcriber.transcribe(audio)
        except Exception as exc:
            self._on_state("error", f"Could not transcribe speech: {exc}")
            return
        finally:
            if not self._stop_event.is_set():
                self._on_state("listening", "")
        free = free if was_armed else strip_wake_word(free, self._wake)
        if free:
            self._on_heard("free", free)

    def run(self) -> None:
        try:
            self._on_state("loading", "Loading the speech model…")
            import sounddevice
            import vosk

            vosk.SetLogLevel(-1)
            model = vosk.Model(str(ensure_voice_model(self._model_folder)))

            def make(words: list[str]):
                # Only words the model knows can be recognised (an application called
                # "Xcode" can't be asked for by that name); leave the others out.
                find = getattr(model, "vosk_model_find_word", None)
                known = [w for w in words if find is None or find(w) >= 0]
                return vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(known + ["[unk]"]))

            recognizer = make(self._vocabulary)
            audio: queue.Queue = queue.Queue(maxsize=80)

            def capture(data, _frames, _time, _status) -> None:
                try:
                    audio.put_nowait(bytes(data))
                except queue.Full:
                    pass  # recognition is behind: dropping a block beats growing a backlog

            with sounddevice.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16", channels=1,
                                            callback=capture):
                self._on_state("listening", "")
                last_sound, told_silent = time.monotonic(), False
                phrase: list[bytes] = []  # the sound of the phrase in progress - held in memory only
                while not self._stop_event.is_set():
                    if self._new_vocabulary is not None:
                        recognizer, self._new_vocabulary = make(self._new_vocabulary), None
                    try:
                        block = audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if any(block):  # a microphone the OS has not granted delivers pure zeros
                        last_sound = time.monotonic()
                        if told_silent:
                            told_silent = False
                            self._on_state("listening", "")
                    elif not told_silent and time.monotonic() - last_sound > 4.0:
                        told_silent = True
                        self._on_state("silent", "The microphone is delivering only silence.")
                    phrase.append(block)
                    del phrase[:-80]  # at most the last 20 seconds
                    if recognizer.AcceptWaveform(block):
                        text = json.loads(recognizer.Result()).get("text", "").strip()
                        sound, phrase = b"".join(phrase), []
                        if text:
                            self.handle_phrase(text, sound, time.monotonic())
        except VoiceError as exc:
            self._on_state("error", str(exc))
        except Exception as exc:  # no microphone, PortAudio failure, damaged model...
            self._on_state("error", f"Voice control could not start: {exc}")
        else:
            self._on_state("stopped", "")

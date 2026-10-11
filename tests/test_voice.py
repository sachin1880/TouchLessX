"""Tests for voice control: the command parser, the wake word, application matching and
voice-driven scrolling. No microphone or speech model is needed - recognition itself is
checked separately against synthesized speech."""
from __future__ import annotations

import zipfile

import pytest

from src.action_controller import ACTIONS, edit_keys
from src.voice import (
    VoiceError, WakeGate, command_vocabulary, ensure_voice_model, match_app, parse_command,
)
from tests.test_features import _Clock
from tests.test_safety import _controller

APPS = ["Google Chrome", "Safari", "Notes", "Visual Studio Code", "Calculator", "Mail", "Chrome Remote Desktop"]


def _parsed(text):
    command = parse_command(text, APPS)
    if command is None:
        return None
    return {"action": command.action, "scroll": (command.direction, command.speed), "open_app": command.app,
            "stop_scroll": "stop", "resume": "resume"}[command.kind]


@pytest.mark.parametrize("spoken, expected", [
    # the user's own examples
    ("scroll this page slowly", ("down", "slow")),
    ("change the tab", "next_tab"),
    ("open google chrome", "Google Chrome"),
    # scrolling
    ("scroll down", ("down", "")),
    ("scroll up", ("up", "")),
    ("please scroll up a little", ("up", "small")),
    ("scroll down a bit", ("down", "small")),
    ("scroll down fast", ("down", "fast")),
    ("can you scroll the page up quickly", ("up", "fast")),
    ("scroll left", ("left", "")),
    ("scroll right slowly", ("right", "slow")),
    ("keep scrolling down", ("down", "keep")),
    ("go down", ("down", "")),
    ("scroll to the top", "scroll_top"),
    ("go to top", "scroll_top"),
    ("go to the bottom", "scroll_bottom"),
    ("page down", "page_down"),
    ("page up", "page_up"),
    ("stop", "stop"),
    ("stop scrolling", "stop"),
    # tabs, windows, browser
    ("next tab", "next_tab"),
    ("switch the tab", "next_tab"),
    ("previous tab", "prev_tab"),
    ("go back a tab", "prev_tab"),
    ("new tab", "new_tab"),
    ("open a new tab", "new_tab"),
    ("close this tab", "close_tab"),
    ("close the window", "close_tab"),
    ("switch app", "switch_app"),
    ("change the window", "switch_app"),
    ("minimize", "minimize_window"),
    ("minimize this window", "minimize_window"),
    ("restore", "restore_window"),
    ("go back", "go_back"),
    ("go forward", "go_forward"),
    ("reload the page", "reload"),
    ("refresh", "reload"),
    ("zoom in", "zoom_in"),
    ("zoom out", "zoom_out"),
    # applications
    ("open chrome", "Google Chrome"),
    ("launch safari", "Safari"),
    ("please open the calculator", "Calculator"),
    ("open visual studio code", "Visual Studio Code"),
    ("open notes app", "Notes"),
    # mouse, text, media
    ("click", "left_click"),
    ("double click", "double_click"),
    ("right click", "right_click"),
    ("copy", "copy"),
    ("copy this", "copy"),
    ("paste", "paste"),
    ("cut", "cut"),
    ("select all", "select_all"),
    ("undo", "undo"),
    ("redo", "redo"),
    ("delete", "delete"),
    ("volume up", "volume_up"),
    ("volume down", "volume_down"),
    ("mute", "mute"),
    ("play", "play_pause"),
    ("pause", "play_pause"),
    ("pause the music", "play_pause"),
    ("next song", "next_track"),
    ("previous track", "prev_track"),
    ("next slide", "next_slide"),
    ("previous slide", "prev_slide"),
    # control
    ("stop everything", "emergency_stop"),
    ("emergency stop", "emergency_stop"),
    ("pause control", "emergency_stop"),
    ("resume", "resume"),
    ("lock", "lock_controls"),
    ("unlock", "confirm"),
])
def test_spoken_commands_are_understood(spoken, expected):
    assert _parsed(spoken) == expected


@pytest.mark.parametrize("spoken", [
    "", "[unk]", "hello there", "what time is it", "open photoshop", "open", "the this please",
    "delete all my files",   # still just "delete": never anything destructive beyond a key press
])
def test_other_speech_is_not_a_command_or_stays_harmless(spoken):
    result = _parsed(spoken)
    assert result in (None, "delete")


def test_every_voice_action_exists_and_has_keys_or_a_handler():
    from src.voice import _SIMPLE

    for _, action, _ in _SIMPLE:
        assert action in ACTIONS, action
    for action in ("next_tab", "prev_tab", "new_tab", "close_tab", "switch_app", "go_back", "go_forward",
                   "reload", "zoom_in", "zoom_out"):
        assert edit_keys(action), action


def test_application_names_match_the_way_people_say_them():
    assert match_app(["chrome"], APPS) == "Google Chrome"           # shortest name containing the word
    assert match_app(["google", "chrome"], APPS) == "Google Chrome"
    assert match_app(["studio", "code"], APPS) == "Visual Studio Code"
    assert match_app(["notes", "app"], APPS) == "Notes"
    assert match_app(["photoshop"], APPS) is None and match_app([], APPS) is None
    words = command_vocabulary(APPS)
    assert {"computer", "scroll", "chrome", "calculator", "studio"} <= set(words)


def test_wake_word_gates_what_counts_as_a_command():
    gate = WakeGate("computer")
    assert gate.feed("scroll down", 0.0) is None                      # not addressed to us
    assert gate.feed("computer scroll down slowly", 1.0) == "scroll down slowly"
    assert gate.feed("hey computer open chrome", 2.0) == "open chrome"

    assert gate.feed("computer", 10.0) is None and gate.armed(10.5)   # wake word alone: now listening
    assert gate.feed("change the tab", 12.0) == "change the tab"
    assert not gate.armed(12.1) and gate.feed("change the tab", 12.5) is None   # only one command per wake

    gate.feed("computer", 20.0)
    assert gate.feed("copy", 27.0) is None                            # too late: the window closed
    assert WakeGate("okay computer").feed("okay computer paste", 0.0) == "paste"


def test_voice_scroll_pushes_coasts_and_stops_on_command():
    clock = _Clock()
    controller, executor, _ = _controller({}, clock=clock)

    def run(seconds):
        for _ in range(int(seconds * 30)):
            clock.now += 1 / 30
            controller.tick()
        return sum(c[1] for c in executor.calls)

    assert controller.voice_scroll("down").label == "Scroll Down"
    one = run(3)
    assert -1500 < one < -500 and controller._momentum is None       # about a screenful, then at rest

    executor.calls.clear()
    controller.voice_scroll("down", "small")
    small = run(3)
    executor.calls.clear()
    controller.voice_scroll("up", "fast")
    fast = run(3)
    assert 0 > small > one and fast > 1500

    executor.calls.clear()
    controller.voice_scroll("down", "slow")                          # "scroll this page slowly"
    assert -260 < run(1) < -180 and controller._momentum is not None  # steady, and still going
    assert controller.stop_scrolling() is True and controller._momentum is None   # "stop"
    executor.calls.clear()
    assert run(1) == 0 and controller.stop_scrolling() is False

    controller.voice_scroll("right", "keep")
    assert executor.calls == [] and run(0.5) > 250                    # sideways, via the horizontal axis
    assert all(c[0] == "hscroll" for c in executor.calls)

    assert controller.voice_scroll("sideways") is None
    controller.enabled["mouse_control"] = False
    assert controller.voice_scroll("down") is None                    # the feature switch applies to voice too


def test_steady_voice_scroll_ends_by_itself_and_on_emergency_stop():
    clock = _Clock()
    controller, executor, safety = _controller({}, clock=clock)
    controller.voice_scroll("down", "slow")
    clock.now += 89
    controller.tick()
    assert controller._momentum is not None
    clock.now += 2
    controller.tick()
    assert controller._momentum is None                               # 90 s limit

    controller.voice_scroll("down", "keep")
    safety.set_paused(True)
    executor.calls.clear()
    controller.tick()
    assert controller._momentum is None and executor.calls == []


def test_speech_model_archive_is_unpacked_only_if_safe(tmp_path, monkeypatch):
    import io
    import src.voice as voice

    def fake_download(members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name in members:
                bundle.writestr(name, b"x")
        monkeypatch.setattr(voice.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(buffer.getvalue()))

    fake_download([f"{voice.MODEL_NAME}/am/final.mdl", f"{voice.MODEL_NAME}/conf/model.conf"])
    assert ensure_voice_model(tmp_path / "ok") == tmp_path / "ok" / voice.MODEL_NAME
    assert ensure_voice_model(tmp_path / "ok").exists()               # second call: already there, no download

    fake_download(["../../escaped.txt", f"{voice.MODEL_NAME}/am/final.mdl"])
    with pytest.raises(VoiceError):
        ensure_voice_model(tmp_path / "evil")
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path.parent / "escaped.txt").exists()

    fake_download(["something-else/readme.txt"])
    with pytest.raises(VoiceError):
        ensure_voice_model(tmp_path / "wrong")

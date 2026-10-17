"""Sound effects for Holo mode, synthesized with numpy when it opens (no audio files to
ship) and played with macOS's own `afplay`, one short process per sound, so playing never
blocks a frame. On other systems, or without afplay, it stays silent.

Names match what HoloScene puts in `scene.sounds`: click, boot, whoosh, grab, throw,
bounce, explode, charge, charge_stop (cuts the charge hum), blast, zap.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 44100
VOLUME = 0.32
MIN_GAP = 0.08          # the same sound at most this often
MAX_PLAYING = 6


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(RATE * seconds)) / RATE


def _tone(freq, seconds: float, decay: float = 8.0) -> np.ndarray:
    """A sine (fixed or swept: freq may be an array) with an exponential fade."""
    t = _t(seconds)
    f = np.broadcast_to(np.asarray(freq, np.float64), t.shape)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    return np.sin(phase) * np.exp(-decay * t)


def _noise(seconds: float, smooth: int = 1, seed: int = 0) -> np.ndarray:
    n = np.random.default_rng(seed).uniform(-1, 1, int(RATE * seconds))
    if smooth > 1:   # a moving average: the bigger, the deeper the rumble
        n = np.convolve(n, np.ones(smooth) / smooth, mode="same")
        n /= max(float(np.abs(n).max()), 1e-6)
    return n


def _fade(signal: np.ndarray, attack: float = 0.005) -> np.ndarray:
    a = min(len(signal), int(RATE * attack))
    out = signal.copy()
    out[:a] *= np.linspace(0, 1, a)
    out[-a:] *= np.linspace(1, 0, a)
    return out


def synthesize() -> dict[str, np.ndarray]:
    sounds: dict[str, np.ndarray] = {}
    sounds["click"] = _tone(1800, 0.05, 60) * 0.6
    notes = [_tone(f, 0.16, 9) for f in (523, 659, 784, 1046)]
    boot = np.zeros(int(RATE * 0.9))
    for i, note in enumerate(notes):
        start = int(RATE * 0.11 * i)
        boot[start:start + len(note)] += note * 0.6
    boot[:len(_t(0.9))] += _tone(np.linspace(80, 220, len(_t(0.9))), 0.9, 2.5) * 0.35
    sounds["boot"] = boot
    whoosh = _noise(0.4, 24, 1) * np.sin(np.pi * _t(0.4) / 0.4) ** 2
    sounds["whoosh"] = whoosh * 0.7
    sounds["grab"] = _tone(np.linspace(380, 760, len(_t(0.09))), 0.09, 18) * 0.7
    sounds["throw"] = _noise(0.25, 12, 2) * np.sin(np.pi * _t(0.25) / 0.25) * 0.6
    sounds["bounce"] = _tone(170, 0.12, 30) * 0.9
    boom = _noise(0.9, 220, 3) * np.exp(-4 * _t(0.9)) + _tone(np.linspace(90, 40, len(_t(0.9))), 0.9, 4) * 0.8
    sounds["explode"] = boom * 0.9
    charge_t = _t(0.8)
    sounds["charge"] = (_tone(np.linspace(180, 940, len(charge_t)), 0.8, 0.0)
                        * (0.55 + 0.45 * np.sin(2 * np.pi * 18 * charge_t)) * np.linspace(0.3, 0.8, len(charge_t)))
    blast = (_noise(0.75, 6, 4) * np.exp(-6 * _t(0.75)) * 0.8
             + _tone(np.linspace(140, 45, len(_t(0.75))), 0.75, 3.5) * 0.9)
    sounds["blast"] = blast
    crackle = _noise(0.3, 1, 5) * (np.random.default_rng(6).random(len(_t(0.3))) > 0.82)
    sounds["zap"] = np.diff(crackle, prepend=0) * 0.5 + _tone(2400, 0.3, 12) * 0.15
    return {name: _fade(np.clip(signal, -1, 1) * VOLUME) for name, signal in sounds.items()}


def write_wav(path: Path, signal: np.ndarray) -> None:
    data = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.tobytes())


class SoundBoard:
    def __init__(self) -> None:
        self._player = shutil.which("afplay")
        self._folder = Path(tempfile.mkdtemp(prefix="touchlessx-holo-"))
        self._files: dict[str, Path] = {}
        self._last: dict[str, float] = {}
        self._playing: list[subprocess.Popen] = []
        self._charge: subprocess.Popen | None = None
        if self._player:
            try:
                for name, signal in synthesize().items():
                    path = self._folder / f"{name}.wav"
                    write_wav(path, signal)
                    self._files[name] = path
            except OSError:
                self._player = None

    @property
    def available(self) -> bool:
        return bool(self._player and self._files)

    def play(self, name: str) -> None:
        if name == "charge_stop":
            self._stop_charge()
            return
        if not self.available or name not in self._files:
            return
        now = time.monotonic()
        if now - self._last.get(name, -1.0) < MIN_GAP:
            return
        self._playing = [p for p in self._playing if p.poll() is None]
        if len(self._playing) >= MAX_PLAYING:
            return
        self._last[name] = now
        if name == "blast":
            self._stop_charge()   # the hum gives way to the blast
        try:
            process = subprocess.Popen([self._player, str(self._files[name])], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except OSError:
            return
        self._playing.append(process)
        if name == "charge":
            self._charge = process

    def _stop_charge(self) -> None:
        if self._charge is not None and self._charge.poll() is None:
            self._charge.terminate()
        self._charge = None

    def close(self) -> None:
        for process in self._playing:
            if process.poll() is None:
                process.terminate()
        shutil.rmtree(self._folder, ignore_errors=True)

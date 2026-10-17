"""Air Notes: write or draw in the air and keep it as a note.

* Pen down = only the index finger raised (the natural "writing with a finger" shape);
  any other hand shape - open hand, fist, two fingers - lifts the pen. A shape has to be
  seen on two frames in a row before the pen changes, so a flicker can't break a stroke.
* The pen is the index fingertip, smoothed (One Euro) so lines are steady but not laggy.
* Ink is kept in the camera view's own 0-1 coordinates (mirrored like a selfie, so writing
  reads the right way round), independent of the size it is shown at.
* A note is saved as a PNG of the ink plus, when macOS can read it, the handwriting as
  text - in Documents/TouchlessX Notes, with a small notes.json index. Nothing leaves the Mac.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .holo_scene import OneEuro

WRIST, INDEX_PIP, INDEX_TIP = 0, 6, 8
FOLDED = ((10, 12), (14, 16), (18, 20))   # (pip, tip) of middle, ring and pinky
SWITCH_FRAMES = 2
MIN_STEP = 0.003         # ignore pen movements smaller than this (camera jitter)
SAVE_SIZE = 1280         # longer side of a saved note image, in pixels
NOTES_DIR = Path.home() / "Documents" / "TouchlessX Notes"


def _dist(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def index_only(landmarks: np.ndarray) -> bool:
    """Only the index finger is raised: its tip is well beyond its middle joint (from the
    wrist), and the other three fingers are folded (tips no further out than their joints)."""
    wrist = landmarks[WRIST]
    index_up = _dist(landmarks[INDEX_TIP], wrist) > _dist(landmarks[INDEX_PIP], wrist) * 1.15
    middle, ring, pinky = (_dist(landmarks[tip], wrist) < _dist(landmarks[pip], wrist) * 1.05 for pip, tip in FOLDED)
    # The middle finger must be down (or ✌ would write); the ring or pinky often half-lifts
    # on its own, so one of those two is enough.
    return index_up and middle and (ring or pinky)


class Pen:
    """Landmarks -> (pen down?, fingertip position) with debouncing and smoothing."""

    def __init__(self) -> None:
        self._fx, self._fy = OneEuro(min_cutoff=1.5, beta=8.0), OneEuro(min_cutoff=1.5, beta=8.0)
        self.down = False
        self._votes = 0

    def update(self, landmarks: np.ndarray | None, t: float) -> tuple[bool, tuple[float, float] | None]:
        if landmarks is None:
            self.down, self._votes = False, 0
            self._fx.reset()
            self._fy.reset()
            return False, None
        wants = index_only(landmarks)
        if wants != self.down:
            self._votes += 1
            if self._votes >= SWITCH_FRAMES:
                self.down, self._votes = wants, 0
        else:
            self._votes = 0
        tip = landmarks[INDEX_TIP]
        point = (self._fx(float(tip[0]), t), self._fy(float(tip[1]), t))
        return self.down, point


class Ink:
    """Strokes in 0-1 view coordinates."""

    def __init__(self) -> None:
        self.strokes: list[list[tuple[float, float]]] = []
        self._drawing = False

    def feed(self, down: bool, point: tuple[float, float] | None) -> None:
        if not down or point is None:
            self._drawing = False
            return
        if not self._drawing:
            self.strokes.append([point])
            self._drawing = True
            return
        last = self.strokes[-1][-1]
        if _dist(point, last) >= MIN_STEP:
            self.strokes[-1].append(point)

    @property
    def empty(self) -> bool:
        return not self.strokes

    def undo(self) -> None:
        if self.strokes:
            self.strokes.pop()
        self._drawing = False

    def clear(self) -> None:
        self.strokes, self._drawing = [], False

    def image(self, aspect: float = 16 / 9, size: int = SAVE_SIZE) -> Image.Image:
        """The ink as a dark-on-white picture of the whole view (aspect = width / height)."""
        width, height = (size, int(size / aspect)) if aspect >= 1 else (int(size * aspect), size)
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        line_width = max(4, size // 160)
        for stroke in self.strokes:
            points = [(x * width, y * height) for x, y in stroke]
            if len(points) > 1:
                draw.line(points, fill=(20, 30, 60), width=line_width, joint="curve")
            for x, y in (points[0], points[-1]):
                r = line_width / 2
                draw.ellipse((x - r, y - r, x + r, y + r), fill=(20, 30, 60))
        return image

    def pixel_strokes(self, width: int, height: int) -> list[list[tuple[float, float]]]:
        """For handwriting recognition, which works in pixels."""
        return [[(x * width, y * height) for x, y in stroke] for stroke in self.strokes]


@dataclass
class Note:
    id: str
    created: float
    text: str
    image: str            # file name inside the notes folder

    @property
    def when(self) -> str:
        return time.strftime("%d %b %Y, %H:%M", time.localtime(self.created))


class NoteStore:
    """The notes folder: PNGs plus notes.json (newest first)."""

    def __init__(self, folder: Path = NOTES_DIR) -> None:
        self.folder = Path(folder)

    @property
    def _index(self) -> Path:
        return self.folder / "notes.json"

    def load(self) -> list[Note]:
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        notes = []
        for item in raw if isinstance(raw, list) else []:
            try:
                note = Note(str(item["id"]), float(item["created"]), str(item.get("text", "")), str(item["image"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (self.folder / note.image).exists():  # a picture deleted by hand drops out
                notes.append(note)
        return sorted(notes, key=lambda n: n.created, reverse=True)

    def _write(self, notes: list[Note]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        temp = self._index.with_suffix(".tmp")
        temp.write_text(json.dumps([asdict(n) for n in notes], indent=1), encoding="utf-8")
        temp.replace(self._index)  # never a half-written index

    def add(self, image: Image.Image, text: str = "", created: float | None = None) -> Note:
        created = time.time() if created is None else created
        note_id = uuid.uuid4().hex[:10]
        name = time.strftime("note-%Y-%m-%d-%H%M%S", time.localtime(created)) + f"-{note_id[:4]}.png"
        self.folder.mkdir(parents=True, exist_ok=True)
        image.save(self.folder / name)
        note = Note(note_id, created, text.strip(), name)
        self._write([note] + self.load())
        return note

    def set_text(self, note_id: str, text: str) -> None:
        notes = self.load()
        for note in notes:
            if note.id == note_id:
                note.text = text.strip()
        self._write(notes)

    def delete(self, note_id: str) -> None:
        notes = self.load()
        for note in notes:
            if note.id == note_id:
                (self.folder / note.image).unlink(missing_ok=True)
        self._write([n for n in notes if n.id != note_id])

    def path(self, note: Note) -> Path:
        return self.folder / note.image

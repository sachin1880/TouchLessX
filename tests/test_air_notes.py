"""Tests for Air Notes: which hand shape is the pen, steady strokes, saving and the notes
folder. No camera or window needed."""
from __future__ import annotations

import json

import numpy as np
from PIL import Image

from src.air_notes import Ink, NoteStore, Pen, index_only

FINGERS = {"index": (5, 6, 8), "middle": (9, 10, 12), "ring": (13, 14, 16), "pinky": (17, 18, 20)}


def _hand(up=("index",), x=0.5, y=0.5) -> np.ndarray:
    """A hand pointing up, wrist at the bottom; fingers in `up` raised, the rest folded."""
    lm = np.zeros((21, 3), np.float32)
    lm[0, :2] = (x, y + 0.4)
    for i, (name, (mcp, pip, tip)) in enumerate(FINGERS.items()):
        fx = x - 0.06 + i * 0.04
        lm[mcp, :2] = (fx, y + 0.2)
        lm[pip, :2] = (fx, y + 0.1)
        lm[pip + 1, :2] = (fx, y + 0.05 if name in up else y + 0.18)
        lm[tip, :2] = (fx, y if name in up else y + 0.22)
    lm[1:5, :2] = (x - 0.1, y + 0.3)   # thumb tucked
    return lm


# ------------------------------------------------------------------ the pen
def test_only_the_index_finger_raised_is_the_pen():
    assert index_only(_hand(("index",)))
    assert not index_only(_hand(("index", "middle", "ring", "pinky")))   # open hand
    assert not index_only(_hand(()))                                       # fist
    assert not index_only(_hand(("index", "middle")))                      # two fingers
    assert index_only(_hand(("index", "ring")))   # a half-lifted ring finger still writes


def test_the_pen_needs_two_frames_to_change():
    pen = Pen()
    assert pen.update(_hand(("index",)), 0.00)[0] is False
    assert pen.update(_hand(("index",)), 0.03)[0] is True
    assert pen.update(_hand(()), 0.06)[0] is True          # one odd frame: still writing
    assert pen.update(_hand(("index",)), 0.09)[0] is True
    assert pen.update(_hand(()), 0.12)[0] is True
    assert pen.update(_hand(()), 0.15)[0] is False


def test_losing_the_hand_lifts_the_pen():
    pen = Pen()
    pen.update(_hand(), 0.0)
    pen.update(_hand(), 0.03)
    assert pen.update(None, 0.06) == (False, None)


def test_the_pen_follows_the_fingertip():
    pen = Pen()
    _down, point = pen.update(_hand(x=0.3, y=0.4), 0.0)
    assert abs(point[0] - (0.3 - 0.06)) < 1e-6 and abs(point[1] - 0.4) < 1e-6


# ------------------------------------------------------------------ ink
def test_strokes_start_and_end_with_the_pen():
    ink = Ink()
    ink.feed(True, (0.1, 0.1))
    ink.feed(True, (0.2, 0.1))
    ink.feed(False, (0.3, 0.1))
    ink.feed(True, (0.5, 0.5))
    assert [len(s) for s in ink.strokes] == [2, 1]


def test_jitter_is_not_ink():
    ink = Ink()
    ink.feed(True, (0.5, 0.5))
    for _ in range(20):
        ink.feed(True, (0.5005, 0.5))
    assert len(ink.strokes[0]) == 1


def test_undo_and_clear():
    ink = Ink()
    for start in (0.1, 0.5):
        ink.feed(True, (start, 0.1))
        ink.feed(True, (start + 0.1, 0.1))
        ink.feed(False, None)
    ink.undo()
    assert len(ink.strokes) == 1
    ink.clear()
    assert ink.empty


def test_the_saved_picture_has_the_ink_where_it_was_drawn():
    ink = Ink()
    ink.feed(True, (0.25, 0.5))
    ink.feed(True, (0.75, 0.5))
    image = ink.image(aspect=16 / 9, size=640)
    assert image.size == (640, 360)
    assert image.getpixel((320, 180)) != (255, 255, 255)     # on the line
    assert image.getpixel((320, 40)) == (255, 255, 255)      # well off it
    assert ink.pixel_strokes(100, 50) == [[(25.0, 25.0), (75.0, 25.0)]]


# ------------------------------------------------------------------ notes folder
def test_notes_are_saved_listed_newest_first_and_deleted(tmp_path):
    store = NoteStore(tmp_path)
    first = store.add(Image.new("RGB", (40, 20), "white"), "  shopping list ", created=1000.0)
    second = store.add(Image.new("RGB", (40, 20), "white"), "", created=2000.0)
    notes = store.load()
    assert [n.id for n in notes] == [second.id, first.id]
    assert notes[1].text == "shopping list" and store.path(notes[1]).exists()
    store.set_text(second.id, "a drawing of a cat")
    assert store.load()[0].text == "a drawing of a cat"
    store.delete(first.id)
    assert [n.id for n in store.load()] == [second.id]
    assert not store.path(first).exists()


def test_a_damaged_index_does_not_break_the_page(tmp_path):
    (tmp_path / "notes.json").write_text("{not json")
    assert NoteStore(tmp_path).load() == []
    (tmp_path / "notes.json").write_text(json.dumps([{"id": "x"}, "junk"]))
    assert NoteStore(tmp_path).load() == []


def test_a_picture_removed_by_hand_drops_out(tmp_path):
    store = NoteStore(tmp_path)
    note = store.add(Image.new("RGB", (10, 10)), "hi")
    store.path(note).unlink()
    assert store.load() == []


def test_no_folder_yet_means_no_notes(tmp_path):
    assert NoteStore(tmp_path / "not-made-yet").load() == []


def test_big_air_writing_is_resized_to_handwriting_size_for_reading():
    from src import handwriting
    tall = [[(300, 250), (300, 550)], [(420, 250), (420, 550)]]   # letters 300 px tall
    image = handwriting.strokes_image(tall)
    assert image.height == handwriting.TEXT_HEIGHT + 2 * handwriting.MARGIN

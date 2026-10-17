"""The Air Notes page: the camera view is a whiteboard. Raise just your index finger to
write or draw, any other hand shape lifts the pen, and a thumbs-up (or Save) keeps it as
a note - a picture plus, when macOS can read it, the handwriting as text.

While "Write with my finger" is on and this page is open, the tracking thread hands each
frame here (App._hand_sink) instead of to the action controller, so writing never moves
the mouse or clicks. Use the real mouse for the buttons.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from collections import deque

import customtkinter as ctk
import cv2
from PIL import Image

from . import handwriting
from .air_notes import Ink, Note, NoteStore, Pen
from .ui_pages import Page
from .ui_widgets import ACCENT, AMBER, GREEN, PANEL_ALT, RED, TEXT_DIM, button, card, font, label, switch

VIEW = (800, 450)            # the whiteboard as shown, in CTk units
TICK_MS = 33
SAVE_HOLD = 0.6              # seconds of thumbs-up to save
THUMB = (192, 108)
INK_BGR = (255, 236, 160)    # light cyan ink on the darkened camera view


class AirNotesPage(Page):
    title = "Air Notes"
    subtitle = ("Raise just your index finger ☝ to write or draw in the air. Thumbs-up 👍 saves it as a note.")

    def build(self) -> None:
        app = self.app
        if not hasattr(app, "air_ink"):
            app.air_ink = Ink()      # kept on the app, so leaving the page doesn't lose unsaved ink
        self.ink: Ink = app.air_ink
        self.pen = Pen()
        self.store = NoteStore()
        self._lock = threading.Lock()
        self._frame_in = None
        self._hands: deque = deque(maxlen=60)
        self._frame = None
        self._aspect = 16 / 9
        self._thumbs_since: float | None = None
        self._thumbs_used = False
        self._saved: queue.Queue = queue.Queue()
        self._confirm_delete = ""
        self._writing = ctk.BooleanVar(value=True)

        top = ctk.CTkFrame(self.body, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(4, 8))
        board = self.section_in(top)
        board.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._view = ctk.CTkLabel(board, text="Starting the camera…", font=font(14), text_color=TEXT_DIM,
                                  fg_color=PANEL_ALT, corner_radius=12, width=VIEW[0], height=VIEW[1])
        self._view.pack(padx=14, pady=(14, 6))
        self._status = label(board, "", 13, True, color=TEXT_DIM)
        self._status.pack(anchor="w", padx=16, pady=(0, 12))

        side = self.section_in(top)
        side.pack(side="left", fill="y")
        label(side, "Note", 15, True).pack(anchor="w", padx=16, pady=(14, 8))
        button(side, "💾  Save note", self.save, "primary", 190, height=40).pack(padx=16, pady=3)
        button(side, "↶  Undo stroke", self._undo, "ghost", 190).pack(padx=16, pady=3)
        button(side, "✕  Clear", self._clear, "ghost", 190).pack(padx=16, pady=3)
        row = ctk.CTkFrame(side, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(14, 4))
        switch(row, self._writing, "Write with my finger").pack(side="left")
        self._writing.trace_add("write", lambda *_: self._sync_sink())
        tips = ("☝  Index finger only = pen down\n✋  Open hand or ✊ fist = pen up\n👍  Thumbs-up = save\n\n"
                "Write big letters, one or two words at a time. Gestures don't move the mouse here while "
                "writing is on.")
        label(side, tips, 11, color=TEXT_DIM, justify="left", wraplength=200).pack(anchor="w", padx=16, pady=(6, 14))

        box = self.section("Saved notes", f"Kept on this Mac in {self.store.folder}. A note is the picture "
                                         "you drew, plus the words macOS could read from it.")
        button(box, "Open folder", self._open_folder, "ghost", 110).place(relx=1.0, x=-18, y=14, anchor="ne")
        self._list = ctk.CTkFrame(box, fg_color="transparent")
        self._list.pack(fill="x", padx=18, pady=(4, 8))
        self.end(box)
        self._draw_notes()
        self._sync_sink()
        self._tick()

    @staticmethod
    def section_in(parent) -> ctk.CTkFrame:
        return card(parent)

    # ------------------------------------------------------------- tracking thread
    def feed(self, frame, result, event, started: float) -> None:
        small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA) \
            if frame is not None else None
        landmarks = result.landmarks.copy() if result is not None else None
        with self._lock:
            self._frame_in = small
            self._hands.append((landmarks, getattr(event, "name", ""), started))  # every one: a stroke needs them all

    def _sync_sink(self) -> None:
        """Hands come here only while this page is shown and writing is on."""
        if self._writing.get() and self.app._current_page == self.title:
            self.app.action_controller.release()
            self.app._hand_sink = self
            if not self.app._tracking_wanted.is_set():
                self.app.start_tracking()  # writing needs the camera
        elif self.app._hand_sink is self:
            self.app._hand_sink = None

    def on_show(self) -> None:
        self._sync_sink()
        self._draw_notes()

    # ------------------------------------------------------------- live loop
    def _tick(self) -> None:
        if not self._view.winfo_exists():
            return
        with self._lock:
            frame, self._frame_in = self._frame_in, None
            hands = list(self._hands)
            self._hands.clear()
        try:
            while True:
                self._finish_save(*self._saved.get_nowait())
        except queue.Empty:
            pass
        if hands:
            for landmarks, gesture, t in hands:
                down, point = self.pen.update(landmarks, t)
                self.ink.feed(down, point)
                self._thumbs(gesture, t)
            if frame is not None:
                self._frame, self._aspect = frame, frame.shape[1] / frame.shape[0]
            if self.winfo_ismapped():
                self._render(down, point, landmarks is not None)
        elif not self.app._camera_running and self.winfo_ismapped():
            self._status.configure(text="The camera is off - start it from the top bar", text_color=AMBER)
        self.after(TICK_MS, self._tick)

    def _thumbs(self, gesture: str, t: float) -> None:
        if gesture != "thumbs_up":
            self._thumbs_since, self._thumbs_used = None, False
            return
        if self._thumbs_since is None:
            self._thumbs_since = t
        if not self._thumbs_used and t - self._thumbs_since >= SAVE_HOLD:
            self._thumbs_used = True  # one save per thumbs-up
            self.save()

    def _render(self, down: bool, point, hand: bool) -> None:
        frame = self._frame
        if frame is None:
            return
        height, width = frame.shape[:2]
        view = (frame * 0.55).astype("uint8")
        for stroke in self.ink.strokes:
            pts = [(int(x * width), int(y * height)) for x, y in stroke]
            if len(pts) == 1:
                cv2.circle(view, pts[0], 4, INK_BGR, -1, cv2.LINE_AA)
            for a, b in zip(pts, pts[1:]):
                cv2.line(view, a, b, INK_BGR, 5, cv2.LINE_AA)
        if point is not None:
            centre = (int(point[0] * width), int(point[1] * height))
            if down:
                cv2.circle(view, centre, 7, (120, 255, 140), -1, cv2.LINE_AA)
            else:
                cv2.circle(view, centre, 10, (255, 255, 255), 2, cv2.LINE_AA)
        image = Image.fromarray(cv2.cvtColor(view, cv2.COLOR_BGR2RGB))
        ratio = min(VIEW[0] / width, VIEW[1] / height)
        size = (int(width * ratio), int(height * ratio))
        shown = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self._view.configure(image=shown, text="")
        self._view.image = shown
        if not hand:
            text, colour = "Show your hand to the camera", TEXT_DIM
        elif down:
            text, colour = "✍  Writing", GREEN
        else:
            text, colour = "Pen up - raise only your index finger ☝ to write", TEXT_DIM
        if self._status.cget("text") != text and not self._status.cget("text").startswith(("Saved", "Saving")):
            self._status.configure(text=text, text_color=colour)

    # ------------------------------------------------------------- actions
    def _undo(self) -> None:
        self.ink.undo()

    def _clear(self) -> None:
        self.ink.clear()

    def save(self) -> None:
        if self.ink.empty:
            self._say("Nothing to save yet - write something first", AMBER)
            return
        image = self.ink.image(self._aspect)
        reading = self.ink.pixel_strokes(1000, int(1000 / self._aspect))
        self.ink.clear()
        self._say("Saving…", AMBER)

        def work():
            text = ""
            if handwriting.available():
                try:
                    picture = handwriting.strokes_image(reading)
                    text = handwriting.recognize(picture) if picture is not None else ""
                except Exception:
                    text = ""
            try:
                note = self.store.add(image, text)
                self._saved.put((note, ""))
            except OSError as exc:
                self._saved.put((None, f"Couldn't save the note: {exc}"))

        threading.Thread(target=work, daemon=True, name="air-note-save").start()

    def _finish_save(self, note: Note | None, error: str) -> None:
        if note is None:
            self._say(error, RED)
            return
        self._say(f"Saved ✓  “{note.text}”" if note.text else "Saved ✓  (a drawing - no words read)", GREEN)
        self.after(2500, lambda: self._status.winfo_exists() and self._status.configure(text=""))
        self._draw_notes()

    def _say(self, text: str, colour) -> None:
        self._status.configure(text=text, text_color=colour)

    # ------------------------------------------------------------- notes list
    def _draw_notes(self) -> None:
        if not self._list.winfo_exists():
            return
        for child in self._list.winfo_children():
            child.destroy()
        notes = self.store.load()
        if not notes:
            label(self._list, "No notes yet. Write something above and give a thumbs-up 👍.", 12,
                  color=TEXT_DIM).pack(anchor="w", pady=6)
            return
        for note in notes[:50]:
            row = ctk.CTkFrame(self._list, fg_color=PANEL_ALT, corner_radius=10)
            row.pack(fill="x", pady=3)
            try:
                with Image.open(self.store.path(note)) as picture:
                    picture.thumbnail((THUMB[0] * 2, THUMB[1] * 2))
                    thumb = ctk.CTkImage(light_image=picture.copy(), dark_image=picture.copy(),
                                         size=_fit(picture.size, THUMB))
                shown = ctk.CTkLabel(row, image=thumb, text="", width=THUMB[0], height=THUMB[1])
                shown.image = thumb
            except OSError:
                shown = ctk.CTkLabel(row, text="(missing)", width=THUMB[0], height=THUMB[1], text_color=TEXT_DIM)
            shown.pack(side="left", padx=10, pady=8)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="both", expand=True, pady=8)
            label(text, note.when, 11, color=TEXT_DIM).pack(anchor="w")
            label(text, note.text or "Drawing", 15, True, color=ACCENT if note.text else TEXT_DIM,
                  justify="left", wraplength=420).pack(anchor="w", pady=(2, 0))
            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.pack(side="right", padx=10)
            if note.text:
                button(actions, "Copy text", lambda n=note: self._copy(n), "ghost", 96).pack(pady=2)
            button(actions, "Open", lambda n=note: self._open(n), "ghost", 96).pack(pady=2)
            sure = self._confirm_delete == note.id
            button(actions, "Sure? Delete" if sure else "Delete", lambda n=note: self._delete(n),
                   "danger" if sure else "ghost", 96).pack(pady=2)

    def _copy(self, note: Note) -> None:
        self.app.clipboard_clear()
        self.app.clipboard_append(note.text)
        self._say(f"Copied “{note.text}” - paste it anywhere with ⌘V", GREEN)

    def _open(self, note: Note) -> None:
        _reveal(str(self.store.path(note)))

    def _open_folder(self) -> None:
        self.store.folder.mkdir(parents=True, exist_ok=True)
        _reveal(str(self.store.folder))

    def _delete(self, note: Note) -> None:
        if self._confirm_delete != note.id:
            self._confirm_delete = note.id  # first click asks; a second one deletes
            self._draw_notes()
            return
        self._confirm_delete = ""
        self.store.delete(note.id)
        self._draw_notes()


def _fit(size, box):
    ratio = min(box[0] / size[0], box[1] / size[1])
    return max(1, int(size[0] * ratio)), max(1, int(size[1] * ratio))


def _reveal(path: str) -> None:
    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", path], check=False)

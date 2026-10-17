"""The Home CCTV gallery: every snapshot, person detection and video clip the selected
camera has on the server. The server deletes them after its retention period (2 days),
so this shows exactly what is still there - nothing is kept on this Mac unless you save it.

Loading happens on the service's own gallery thread (CctvService.refresh_gallery); this
module only draws from `gallery_snapshot()`. Photos open in a large viewer with
previous / next and Save; a clip is downloaded to a temporary file and opened in the
Mac's video player.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

from .cctv_client import CctvError, MediaItem
from .ui_widgets import ACCENT, AMBER, BG, PANEL_ALT, RED, TEXT_DIM, button, font, label

TILE = (208, 150)
COLUMNS = 4
PAGE_SIZE = 24
AUTO_REFRESH_S = 30.0
VIEWER_BOX = (1000, 640)
FILTERS = {"All": None, "Snapshots": "snapshot", "Person detected": "person", "Videos": "video"}
KIND_TEXT = {"snapshot": ("📸 Snapshot", ACCENT), "person": ("🧍 Person detected", AMBER), "video": ("🎬 Video", TEXT_DIM)}


def when(iso: str, now: datetime | None = None) -> str:
    """"Today 11:33", "Yesterday 18:02" or "15 Oct 09:10", in this Mac's time zone."""
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return "unknown time"
    today = (now or datetime.now().astimezone()).date()
    clock = moment.strftime("%H:%M")
    if moment.date() == today:
        return f"Today {clock}"
    if moment.date() == today - timedelta(days=1):
        return f"Yesterday {clock}"
    return moment.strftime("%d %b ") + clock


def visible(items: list[MediaItem], choice: str) -> list[MediaItem]:
    kind = FILTERS.get(choice)
    return [i for i in items if kind is None or i.kind == kind]


def decode(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except (OSError, ValueError):
        return None


def fitted(image: Image.Image, box: tuple[int, int]) -> ctk.CTkImage:
    ratio = min(box[0] / image.width, box[1] / image.height)
    size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return ctk.CTkImage(light_image=image, dark_image=image, size=size)


class GalleryView:
    """The gallery section's contents. The page calls `tick()` on its refresh loop."""

    def __init__(self, parent, app) -> None:
        self.app = app
        self._thumbs: dict[str, ctk.CTkImage] = {}
        self._shown_key = None
        self._limit = PAGE_SIZE
        self._video_note = ""
        self._last_auto = 0.0

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 6))
        self._filter = ctk.CTkSegmentedButton(row, values=list(FILTERS), command=lambda _v: self._changed(),
                                              font=font(12, True))
        self._filter.set("All")
        self._filter.pack(side="left")
        self._status = label(row, "", 11, color=TEXT_DIM)
        self._status.pack(side="left", padx=12)
        button(row, "↻ Refresh", self.refresh, "ghost", 100).pack(side="right")

        self._grid = ctk.CTkFrame(parent, fg_color="transparent")
        self._grid.pack(fill="x", padx=18, pady=(0, 4))
        for column in range(COLUMNS):
            self._grid.grid_columnconfigure(column, weight=1, uniform="tile")
        self._more = button(parent, "Show more", self._show_more, "ghost", 120)

    # ---------------------------------------------------------------- actions
    def refresh(self) -> None:
        self._last_auto = time.monotonic()
        self.app.cctv.refresh_gallery()

    def _changed(self) -> None:
        self._limit = PAGE_SIZE
        self._shown_key = None

    def _show_more(self) -> None:
        self._limit += PAGE_SIZE
        self._shown_key = None

    def tick(self) -> None:
        """Redraws when something changed; reloads every AUTO_REFRESH_S while shown."""
        if time.monotonic() - self._last_auto > AUTO_REFRESH_S:
            self.refresh()
        gallery = self.app.cctv.gallery_snapshot()
        choice = self._filter.get()
        status = self._status_text(gallery, choice)
        if self._status.cget("text") != status[0]:
            self._status.configure(text=status[0], text_color=status[1])
        key = (gallery.device_id, tuple(i.id for i in gallery.items), choice, self._limit, gallery.loaded_at > 0,
               gallery.message)
        if key != self._shown_key:
            self._shown_key = key
            self._draw(gallery, choice)

    def _status_text(self, gallery, choice: str):
        if self._video_note:
            return self._video_note, AMBER
        if gallery.loading:
            return "Loading…", AMBER
        if gallery.message:
            return gallery.message, RED
        items = visible(gallery.items, choice)
        if not gallery.loaded_at:
            return "", TEXT_DIM
        photos = sum(1 for i in items if i.kind != "video")
        clips = len(items) - photos
        parts = ([f"{photos} photo{'s' * (photos != 1)}"] if choice != "Videos" else []) + \
                ([f"{clips} clip{'s' * (clips != 1)}"] if choice in ("All", "Videos") else [])
        return " · ".join(parts) + " · kept 2 days on the server", TEXT_DIM

    # ---------------------------------------------------------------- drawing
    def _draw(self, gallery, choice: str) -> None:
        for child in self._grid.winfo_children():
            child.destroy()
        self._more.pack_forget()
        items = visible(gallery.items, choice)
        if not items:
            label(self._grid, self._empty_text(gallery, choice), 12, color=TEXT_DIM, justify="left").grid(
                row=0, column=0, columnspan=COLUMNS, sticky="w", pady=8)
            return
        photos = [i for i in items if i.kind != "video"]
        for index, item in enumerate(items[:self._limit]):
            tile = self._tile(item, photos)
            tile.grid(row=index // COLUMNS, column=index % COLUMNS, padx=4, pady=4, sticky="nsew")
        if len(items) > self._limit:
            self._more.configure(text=f"Show more ({len(items) - self._limit} more)")
            self._more.pack(anchor="w", padx=18, pady=(0, 6))

    @staticmethod
    def _empty_text(gallery, choice: str) -> str:
        if not gallery.loaded_at:
            return "Loading…" if gallery.loading else "Nothing loaded yet."
        if choice == "Videos":
            return ("No video clips. Continuous recording is switched off on the camera phone for now, "
                    "so only photos are saved.")
        if choice == "Person detected":
            return ("No person detected in the last 2 days. The camera checks for a face every 10 seconds "
                    "while it streams.")
        return "Nothing in the last 2 days. Take a snapshot, or wait for the camera to detect someone."

    def _tile(self, item: MediaItem, photos: list[MediaItem]) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(self._grid, fg_color=PANEL_ALT, corner_radius=10)
        kind_text, colour = KIND_TEXT.get(item.kind, ("", TEXT_DIM))
        if item.kind == "video":
            minutes, seconds = divmod(item.duration, 60)
            length = f"{minutes}:{seconds:02d}" if minutes else f"{seconds} s"
            open_it = button(tile, f"▶  Play clip\n{length}", lambda i=item: self._play(i), "ghost",
                             TILE[0], height=TILE[1])
        else:
            thumb = self._thumb(item)
            index = photos.index(item)
            open_it = ctk.CTkButton(tile, image=thumb, text="" if thumb else "Can't show this photo",
                                    width=TILE[0], height=TILE[1], fg_color=BG, hover_color=ACCENT,
                                    corner_radius=8, command=lambda i=index: PhotoViewer(self.app, photos, i))
        open_it.pack(padx=6, pady=(6, 2))
        label(tile, kind_text, 11, True, color=colour).pack(anchor="w", padx=10)
        label(tile, when(item.at), 11, color=TEXT_DIM).pack(anchor="w", padx=10, pady=(0, 8))
        return tile

    def _thumb(self, item: MediaItem) -> ctk.CTkImage | None:
        if item.id not in self._thumbs:
            image = decode(item.image)
            if image is None:
                return None
            image.thumbnail((TILE[0] * 2, TILE[1] * 2))  # 2x for sharp tiles on Retina screens
            self._thumbs[item.id] = fitted(image, TILE)
        return self._thumbs[item.id]

    # ---------------------------------------------------------------- videos
    def _play(self, item: MediaItem) -> None:
        self._video_note = "Downloading the clip…"

        def work():
            try:
                path = self.app.cctv.download_video(item, tempfile.gettempdir())
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.run([opener, path], check=False)
                self._video_note = ""
            except CctvError as exc:
                self._video_note = f"Couldn't download the clip: {exc}"
            except OSError:
                self._video_note = "Couldn't save the clip to a temporary file"

        threading.Thread(target=work, daemon=True, name="cctv-video").start()


class PhotoViewer(ctk.CTkToplevel):
    """One photo, large. ◀ / ▶ (or the arrow keys) step through the photos shown in the
    gallery; Save writes the original JPEG wherever you choose."""

    def __init__(self, app, photos: list[MediaItem], index: int) -> None:
        super().__init__(app)
        self.photos, self.index = photos, index
        self.configure(fg_color=BG)
        self.geometry("1040x740")
        self._image = ctk.CTkLabel(self, text="", fg_color=PANEL_ALT, corner_radius=12,
                                   width=VIEWER_BOX[0], height=VIEWER_BOX[1])
        self._image.pack(padx=20, pady=(20, 8))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))
        button(row, "◀", lambda: self._step(-1), "ghost", 50).pack(side="left")
        button(row, "▶", lambda: self._step(1), "ghost", 50).pack(side="left", padx=(6, 12))
        self._caption = label(row, "", 13, True)
        self._caption.pack(side="left")
        button(row, "Close", self.destroy, "ghost", 80).pack(side="right")
        button(row, "Save…", self._save, "primary", 90).pack(side="right", padx=8)
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<Escape>", lambda _e: self.destroy())
        self._show()
        self.after(50, self.focus_force)

    def _step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.photos)
        self._show()

    def _show(self) -> None:
        item = self.photos[self.index]
        kind_text, _ = KIND_TEXT.get(item.kind, ("", TEXT_DIM))
        caption = f"{kind_text} · {when(item.at)} · {self.index + 1} of {len(self.photos)}"
        self.title(f"Home CCTV · {kind_text} · {when(item.at)}")
        self._caption.configure(text=caption)
        image = decode(item.image)
        if image is None:
            self._image.configure(image=None, text="This photo can't be shown")
            self._image.image = None
            return
        shown = fitted(image, VIEWER_BOX)
        self._image.configure(image=shown, text="")
        self._image.image = shown  # keep a reference - CTkLabel won't hold one for us

    def _save(self) -> None:
        item = self.photos[self.index]
        stamp = when(item.at).replace(" ", "-").replace(":", "")
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".jpg", initialfile=f"cctv-{stamp}.jpg",
                                            filetypes=[("JPEG image", "*.jpg")])
        if path and item.image:
            with open(path, "wb") as handle:
                handle.write(item.image)

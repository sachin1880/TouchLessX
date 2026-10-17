"""The Home CCTV page: sign in to a HomeSmartCCTV server, pick a camera, send it
commands and follow each one to its result. Separate from Smart Home (webhooks):
cameras are accounts and devices on a server, with commands that report back.

All network work happens in `App.cctv` (cctv_client.CctvService) and `App.cctv_live`
(cctv_live.LiveView) on their own threads; this page only queues requests and redraws
from `snapshot()`, `status()` and `frame()`."""
from __future__ import annotations

import io
import time
from datetime import datetime, timezone

import customtkinter as ctk
from PIL import Image

from . import cctv_live
from .cctv_commands import CCTV_COMMANDS
from .cctv_client import CctvError, validate_server_url
from .ui_cctv_gallery import GalleryView
from .ui_pages import Page
from .ui_widgets import ACCENT, AMBER, GREEN, PANEL_ALT, RED, TEXT_DIM, button, entry, font, label

REFRESH_MS = 500
LIVE_MS = 66                  # ~15 redraws a second at most, and only when a new frame arrived
VIDEO_BOX = (640, 360)
SNAPSHOT_BOX = (480, 300)
LIVE_STATUS = {cctv_live.IDLE: ("Not watching", TEXT_DIM), cctv_live.CONNECTING: ("Connecting…", AMBER),
               cctv_live.WAITING: ("Waiting for the camera", AMBER),
               cctv_live.NEGOTIATING: ("Connecting to the camera…", AMBER), cctv_live.LIVE: ("● Live", GREEN),
               cctv_live.REPLACED: ("Closed", TEXT_DIM), cctv_live.ENDED: ("Disconnected", RED),
               cctv_live.FAILED: ("Failed", RED)}
STATUS_COLOURS = {"SUCCEEDED": GREEN, "FAILED": RED, "EXPIRED": RED, "CANCELLED": TEXT_DIM}
STATUS_TEXT = {"SENDING": "Sending…", "PENDING": "Waiting for camera", "DELIVERED": "Delivered",
               "ACKNOWLEDGED": "Camera is doing it", "SUCCEEDED": "✓ Done", "FAILED": "✗ Failed",
               "EXPIRED": "✗ Camera didn't respond", "CANCELLED": "Cancelled"}


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    seconds = int((datetime.now(timezone.utc) - then).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def device_summary(device: dict) -> tuple[str, object]:
    """One line for the camera list, and its colour."""
    if not device.get("is_online"):
        return f"Offline · last seen {_ago(device.get('last_seen'))}", TEXT_DIM
    parts = ["Online"]
    if device.get("battery_level") is not None:
        parts.append(f"🔋 {device['battery_level']}%")
    network = {"WIFI": "Wi-Fi", "CELLULAR": "Mobile data", "DISCONNECTED": "No network"}.get(
        device.get("network_status") or "")
    if network:
        parts.append(network)
    parts.append("Streaming" if device.get("camera_status") == "STREAMING" else "Camera idle")
    return " · ".join(parts), GREEN


def fit(size: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """The largest size with the image's proportions that fits in the box."""
    ratio = min(box[0] / size[0], box[1] / size[1])
    return max(1, int(size[0] * ratio)), max(1, int(size[1] * ratio))


def _show_image(target: ctk.CTkLabel, image: Image.Image | None, box: tuple[int, int], text: str = "") -> None:
    if image is None:
        target.configure(image=None, text=text)
        target.image = None
        return
    ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=fit(image.size, box))
    target.configure(image=ctk_image, text="")
    target.image = ctk_image  # keep a reference - CTkLabel won't hold one for us


class HomeCctvPage(Page):
    title = "Home CCTV"
    subtitle = "Control your HomeSmartCCTV cameras with gestures and voice. Separate from Smart Home webhooks."

    def build(self) -> None:
        app = self.app
        service = app.cctv
        state = service.snapshot()

        box = self.section("Connection", "TouchlessX signs in to your HomeSmartCCTV server. Your password is "
                                         "used once and not stored; the session is kept in the macOS Keychain.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        label(row, "Server", 13, True, width=70, anchor="w").pack(side="left")
        self._server = entry(row, "http://localhost:8000/api", 320)
        self._server.insert(0, app.config_data["cctv_server_url"])
        self._server.pack(side="left", padx=(0, 8))
        button(row, "Save", self._save_server, "ghost", 70).pack(side="left")

        if not state["signed_in"]:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            label(row, "Sign in", 13, True, width=70, anchor="w").pack(side="left")
            self._email = entry(row, "Email", 220)
            if app.config_data["cctv_email"]:
                self._email.insert(0, app.config_data["cctv_email"])
            self._email.pack(side="left", padx=(0, 6))
            self._password = entry(row, "Password", 160, show="•")
            self._password.pack(side="left", padx=(0, 6))
            self._password.bind("<Return>", lambda _e: self._sign_in())
            button(row, "Sign in", self._sign_in, "primary", 90).pack(side="left")
        else:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            label(row, f"● Signed in{' as ' + app.config_data['cctv_email'] if app.config_data['cctv_email'] else ''}",
                  12, True, color=GREEN).pack(side="left")
            button(row, "Sign out", self._sign_out, "ghost", 90).pack(side="right")
        self._message = self.note(box, "")
        if not service.tokens.persistent:
            self.note(box, "The system credential store isn't available, so you'll sign in again after a restart.",
                      AMBER)
        self.end(box)

        box = self.section("Cameras", "Choose the camera that gestures and voice control.")
        button(box, "↻ Refresh", service.refresh_devices, "ghost", 100).place(relx=1.0, x=-18, y=14, anchor="ne")
        self._devices_frame = ctk.CTkFrame(box, fg_color="transparent")
        self._devices_frame.pack(fill="x", padx=18, pady=4)
        self._devices_shown = None
        self.end(box)

        box = self.section("Live view", "Video comes straight from the phone over your network; the server only "
                                        "helps the two connect. A camera serves one viewer at a time, so watching "
                                        "here closes the live view in the phone viewer app (and the other way round).")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        self._live_button = button(row, "▶  Watch live", self._toggle_live, "primary", 150)
        self._live_button.pack(side="left")
        self._live_status = label(row, "", 12, True, color=TEXT_DIM)
        self._live_status.pack(side="left", padx=12)
        self._video = ctk.CTkLabel(box, text="Not watching", font=font(13), text_color=TEXT_DIM, fg_color=PANEL_ALT,
                                   corner_radius=10, width=VIDEO_BOX[0], height=VIDEO_BOX[1])
        self._video.pack(anchor="w", padx=18, pady=6)
        self._live_shown = None
        self._frame_shown = None
        self.end(box)

        box = self.section("Commands", "Sent to the selected camera. With gestures or voice, Flip Camera and Take "
                                       "Snapshot wait for a thumbs-up 👍 (or \"computer, confirm\") first.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        for name, command in CCTV_COMMANDS.items():
            glyph = {"refresh_status": "🔄", "switch_camera": "🔁", "capture_snapshot": "📸"}.get(name, "•")
            button(row, f"{glyph}  {command.label}", lambda n=name: service.send(n), "primary", 150).pack(
                side="left", padx=(0, 8))
        label(box, "Latest snapshot", 13, True).pack(anchor="w", padx=18, pady=(10, 2))
        self._snapshot_caption = label(box, "", 11, color=TEXT_DIM)
        self._snapshot_caption.pack(anchor="w", padx=18)
        self._snapshot = ctk.CTkLabel(box, text="No snapshot yet: take one and it appears here.", font=font(12),
                                      text_color=TEXT_DIM, fg_color=PANEL_ALT, corner_radius=10,
                                      width=SNAPSHOT_BOX[0], height=80)
        self._snapshot.pack(anchor="w", padx=18, pady=(2, 0))
        self._snapshot_shown = None
        label(box, "Recent", 13, True).pack(anchor="w", padx=18, pady=(10, 2))
        self._recent_frame = ctk.CTkFrame(box, fg_color="transparent")
        self._recent_frame.pack(fill="x", padx=18, pady=(0, 6))
        self._recent_shown = None
        self.end(box)

        box = self.section("Gallery", "Snapshots, person detections and video clips from the selected camera. "
                                      "The server keeps them for 2 days, then deletes them for good; click a photo "
                                      "to see it large or save a copy.")
        self._gallery = GalleryView(box, app)
        self.end(box)

        box = self.section("Gestures and voice")
        self.note(box, "In Gesture Studio, map any gesture to CCTV: Camera Status, CCTV: Flip Camera or CCTV: Take "
                       "Snapshot.\nVoice: \"computer, camera status\" · \"computer, flip camera\" · \"computer, take "
                       "snapshot\" · then \"computer, confirm\".\nPause, emergency stop and practice mode apply as "
                       "everywhere: in practice mode nothing is sent to the camera.")
        button(box, "Open Gesture Studio", lambda: app.show_page("Gesture Studio"), "ghost", 170).pack(
            anchor="w", padx=18, pady=(8, 0))
        self.end(box)
        self._refresh()
        self._tick_live()

    # ---------------------------------------------------------------- actions
    def _save_server(self) -> None:
        url = self._server.get().strip()
        try:
            url = validate_server_url(url)
        except CctvError as exc:
            self._message.configure(text=f"● {exc}", text_color=RED)
            return
        self.app.set_option("cctv_server_url", url)
        self.app.cctv.set_server(url)
        self.rebuild()

    def _sign_in(self) -> None:
        email, password = self._email.get().strip(), self._password.get()
        if not email or not password:
            self._message.configure(text="● Enter your email and password", text_color=AMBER)
            return
        self.app.set_option("cctv_email", email)
        self.app.cctv.sign_in(email, password)
        self._password.delete(0, "end")
        self._wait_for_sign_in(time.monotonic() + 15)

    def _wait_for_sign_in(self, deadline: float) -> None:
        if not self.winfo_exists():
            return
        if self.app.cctv.signed_in:
            self.rebuild()
        elif time.monotonic() < deadline and (self.app.cctv.busy or time.monotonic() < deadline - 14):
            self.after(300, lambda: self._wait_for_sign_in(deadline))

    def _sign_out(self) -> None:
        self.app.cctv_live.stop()
        self.app.cctv.sign_out()
        self.rebuild()

    def _select(self, device_id: str) -> None:
        self.app.cctv.select_device(device_id)
        self.app.set_option("cctv_device_id", device_id)
        self._devices_shown = None  # redraw the selection
        self._gallery.refresh()
        if self.app.cctv_live.running:
            self._toggle_live(restart=True)  # follow the newly chosen camera

    def _toggle_live(self, restart: bool = False) -> None:
        live = self.app.cctv_live
        if live.running and not restart:
            live.stop()
            return
        session = self.app.cctv.live_session()
        if session is None:
            live.stop()
            self._live_status.configure(text="Sign in and choose a camera first", text_color=AMBER)
            self._live_shown = None
            return
        live.start(*session)

    # ---------------------------------------------------------------- live
    def _refresh(self) -> None:
        if not self._recent_frame.winfo_exists():
            return  # rebuilt: the new build runs its own loop
        if self.winfo_ismapped():
            state = self.app.cctv.snapshot()
            self._message.configure(text=f"● {state['message']}" if state["message"] else "",
                                    text_color=AMBER if state["busy"] else RED if state["error"] else TEXT_DIM)
            devices_key = (state["device_id"], tuple((d.get("id"), d.get("is_online"), d.get("battery_level"),
                                                      d.get("camera_status"), d.get("network_status"))
                                                     for d in state["devices"]))
            if devices_key != self._devices_shown:
                self._devices_shown = devices_key
                self._draw_devices(state)
            recent_key = tuple((c.idempotency_key, c.status, c.error) for c in state["recent"])
            if recent_key != self._recent_shown:
                self._recent_shown = recent_key
                self._draw_recent(state)
            if state["last_snapshot"] is not self._snapshot_shown:
                self._snapshot_shown = state["last_snapshot"]
                self._draw_snapshot(state["last_snapshot"])
            self._gallery.tick()
        self.after(REFRESH_MS, self._refresh)

    def _tick_live(self) -> None:
        if not self._video.winfo_exists():
            return  # rebuilt: the new build runs its own loop
        if self.winfo_ismapped():
            live = self.app.cctv_live
            status, message = live.status()
            if (status, message) != self._live_shown:
                self._live_shown = (status, message)
                text, colour = LIVE_STATUS.get(status, ("", TEXT_DIM))
                self._live_status.configure(text=f"{text} · {message}" if message and status != cctv_live.IDLE
                                            else text, text_color=colour)
                self._live_button.configure(text="■  Stop" if live.running else "▶  Watch live")
            seq, image = live.frame()
            key = (seq, image is None, image is None and status)
            if key != self._frame_shown:
                self._frame_shown = key
                if image is None:
                    placeholder = "Waiting for video…" if live.running else "Not watching"
                    _show_image(self._video, None, VIDEO_BOX, placeholder)
                else:
                    _show_image(self._video, image, VIDEO_BOX)
        self.after(LIVE_MS, self._tick_live)

    def _draw_snapshot(self, snapshot) -> None:
        if snapshot is None:
            return
        try:
            image = Image.open(io.BytesIO(snapshot.image))
            image.load()
        except (OSError, ValueError):
            self._snapshot_caption.configure(text="The snapshot couldn't be shown (not a readable image)")
            return
        taken = time.strftime("%H:%M:%S", time.localtime(snapshot.taken_at))
        self._snapshot_caption.configure(text=f"📸 {snapshot.device_name} · {taken}")
        _show_image(self._snapshot, image.convert("RGB"), SNAPSHOT_BOX)

    def _draw_devices(self, state: dict) -> None:
        for child in self._devices_frame.winfo_children():
            child.destroy()
        if not state["signed_in"]:
            label(self._devices_frame, "Sign in to see your cameras.", 12, color=TEXT_DIM).pack(anchor="w")
            return
        if not state["devices"]:
            label(self._devices_frame, "No cameras yet - open HomeSmartCCTV on the camera phone in Camera mode.",
                  12, color=TEXT_DIM).pack(anchor="w")
            return
        for device in state["devices"]:
            selected = device.get("id") == state["device_id"]
            row = ctk.CTkFrame(self._devices_frame, fg_color=PANEL_ALT, corner_radius=10,
                               border_width=2 if selected else 0, border_color=ACCENT)
            row.pack(fill="x", pady=3)
            summary, colour = device_summary(device)
            # Every child keeps clear of the row's edge (pady/padx), or it paints over the
            # selection border.
            label(row, "●", 16, color=colour, width=26).pack(side="left", padx=(12, 0), pady=10)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=(4, 0), pady=8)
            label(text, device.get("device_name", "Camera"), 13, True).pack(anchor="w")
            label(text, summary, 11, color=TEXT_DIM).pack(anchor="w")
            if selected:
                label(row, "✓ Selected", 12, True, color=ACCENT).pack(side="right", padx=14, pady=8)
            else:
                button(row, "Use", lambda d=device["id"]: self._select(d), "ghost", 70).pack(
                    side="right", padx=10, pady=8)

    def _draw_recent(self, state: dict) -> None:
        for child in self._recent_frame.winfo_children():
            child.destroy()
        if not state["recent"]:
            label(self._recent_frame, "No commands sent yet.", 12, color=TEXT_DIM).pack(anchor="w")
            return
        for sent in state["recent"]:
            row = ctk.CTkFrame(self._recent_frame, fg_color=PANEL_ALT, corner_radius=8)
            row.pack(fill="x", pady=2)
            label(row, time.strftime("%H:%M:%S", time.localtime(sent.sent_at)), 11, color=TEXT_DIM,
                  width=70).pack(side="left", padx=(10, 4), pady=6)
            label(row, f"{sent.label} → {sent.device_name}", 12, True, anchor="w").pack(side="left")
            text = STATUS_TEXT.get(sent.status, sent.status.title())
            if sent.error and sent.status in ("FAILED", "SENDING"):
                text += f" ({sent.error})"
            if sent.status in ("PENDING", "DELIVERED") and sent.command_id:
                button(row, "Cancel", lambda c=sent.command_id: self.app.cctv.cancel(c), "ghost", 70).pack(
                    side="right", padx=6)
            label(row, text, 12, True, color=STATUS_COLOURS.get(sent.status, AMBER)).pack(side="right", padx=10)

    def on_show(self) -> None:
        self.rebuild()

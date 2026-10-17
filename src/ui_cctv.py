"""The Home CCTV page: sign in to a HomeSmartCCTV server, pick a camera, send it
commands and follow each one to its result. Separate from Smart Home (webhooks):
cameras are accounts and devices on a server, with commands that report back.

All network work happens in `App.cctv` (cctv_client.CctvService) on its own thread;
this page only queues requests and redraws from `snapshot()`."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import customtkinter as ctk

from .cctv_commands import CCTV_COMMANDS
from .cctv_client import CctvError, validate_server_url
from .ui_pages import Page
from .ui_widgets import ACCENT, AMBER, GREEN, PANEL_ALT, RED, TEXT_DIM, button, entry, label

REFRESH_MS = 500
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
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=18)
        button(top, "↻ Refresh", service.refresh_devices, "ghost", 100).pack(side="right")
        self._devices_frame = ctk.CTkFrame(box, fg_color="transparent")
        self._devices_frame.pack(fill="x", padx=18, pady=4)
        self._devices_shown = None
        self.end(box)

        box = self.section("Commands", "Sent to the selected camera. With gestures or voice, Flip Camera and Take "
                                       "Snapshot wait for a thumbs-up 👍 (or \"computer, confirm\") first.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=6)
        for name, command in CCTV_COMMANDS.items():
            glyph = {"refresh_status": "🔄", "switch_camera": "🔁", "capture_snapshot": "📸"}.get(name, "•")
            button(row, f"{glyph}  {command.label}", lambda n=name: service.send(n), "primary", 150).pack(
                side="left", padx=(0, 8))
        label(box, "Recent", 13, True).pack(anchor="w", padx=18, pady=(10, 2))
        self._recent_frame = ctk.CTkFrame(box, fg_color="transparent")
        self._recent_frame.pack(fill="x", padx=18, pady=(0, 6))
        self._recent_shown = None
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
        self.app.cctv.sign_out()
        self.rebuild()

    def _select(self, device_id: str) -> None:
        self.app.cctv.select_device(device_id)
        self.app.set_option("cctv_device_id", device_id)
        self._devices_shown = None  # redraw the selection

    # ---------------------------------------------------------------- live
    def _refresh(self) -> None:
        if not self._recent_frame.winfo_exists():
            return  # rebuilt: the new build runs its own loop
        if self.winfo_ismapped():
            state = self.app.cctv.snapshot()
            self._message.configure(text=f"● {state['message']}" if state["message"] else "",
                                    text_color=TEXT_DIM if not state["busy"] else AMBER)
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
        self.after(REFRESH_MS, self._refresh)

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
            label(row, "●", 16, color=colour, width=26).pack(side="left", padx=(10, 0), pady=8)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True)
            label(text, device.get("device_name", "Camera"), 13, True).pack(anchor="w")
            label(text, summary, 11, color=TEXT_DIM).pack(anchor="w")
            if selected:
                label(row, "Selected", 12, True, color=ACCENT).pack(side="right", padx=12)
            else:
                button(row, "Use", lambda d=device["id"]: self._select(d), "ghost", 70).pack(side="right", padx=8)

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

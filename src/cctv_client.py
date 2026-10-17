"""Talking to the HomeSmartCCTV backend: sign-in, the camera list, and sending camera
commands and following them to their result.

* All network work runs on one background thread (`CctvService`) - never on the
  tracking thread, so a slow or unreachable server can't stall gestures.
* A command's idempotency key is chosen once, when the gesture fires, and reused on
  every retry: a network retry can never make the camera act twice. Only network
  failures are retried, a bounded number of times; a refusal from the server is final.
* The access token is kept in the operating system's credential store (the macOS
  Keychain) through `keyring`, never in settings files or logs. The password is used
  once to sign in and not stored. Without `keyring` the token lives in memory only.
* TLS: an https:// server is verified with the system's certificates (urllib's
  default). Plain http:// is accepted for a server on this computer or the home network.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from .cctv_commands import CCTV_COMMANDS

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "TouchlessX-HomeCCTV"
REQUEST_TIMEOUT = 6.0
NETWORK_ATTEMPTS = 3          # a network failure is retried, with the same idempotency key
RETRY_DELAYS = (0.5, 1.5)
POLL_INTERVAL = 0.5           # how often an unfinished command is checked
FOLLOW_SECONDS = 120.0        # how long to keep checking a command for its outcome
TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"}
RECENT_LIMIT = 12
VIDEO_TIMEOUT = 60.0          # a clip is up to tens of MB
PHOTO_KINDS = {"MANUAL_CAPTURE": "snapshot", "PERSON_DETECTED": "person"}


class CctvError(Exception):
    """kind: "config" (bad server address), "auth" (signed out / refused credentials),
    "network" (server unreachable), "http" (the server refused the request)."""

    def __init__(self, kind: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.kind, self.status = kind, status


def validate_server_url(url: str) -> str:
    """The API base URL, e.g. http://localhost:8000/api. http:// only for local addresses."""
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CctvError("config", "The server address must start with http:// or https://")
    if parsed.scheme == "http" and not _is_local(parsed.hostname):
        raise CctvError("config", "Use https:// for a server outside this computer and home network")
    return url


def _is_local(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class CctvClient:
    """Thin, synchronous HTTP client for the backend API. Call it off the UI and
    tracking threads (CctvService does)."""

    def __init__(self, base_url: str, token: str | None = None, opener=urllib.request.urlopen,
                 timeout: float = REQUEST_TIMEOUT) -> None:
        self.base_url = validate_server_url(base_url)
        self.token = token
        self._opener = opener
        self._timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None, auth: bool = True, raw: bool = False,
                 timeout: float | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if not raw:
            request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if auth:
            if not self.token:
                raise CctvError("auth", "Not signed in")
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self._opener(request, timeout=timeout or self._timeout) as response:
                content = response.read()
        except urllib.error.HTTPError as exc:
            detail = _detail(exc)
            if exc.code == 401:
                raise CctvError("auth", "Signed out - please sign in again", 401) from exc
            raise CctvError("http", detail or f"Server error {exc.code}", exc.code) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise CctvError("network", "Can't reach the CCTV server") from exc
        if raw:
            return content
        return json.loads(content) if content else None

    def login(self, email: str, password: str) -> str:
        try:
            body = self._request("POST", "/auth/login", {"email": email, "password": password}, auth=False)
        except CctvError as exc:
            if exc.status == 401:  # not "signed out": there was no session to lose
                raise CctvError("auth", "Wrong email or password", 401) from exc
            raise
        token = (body or {}).get("access_token")
        if not token:
            raise CctvError("auth", "The server did not return a session")
        self.token = token
        return token

    def devices(self) -> list[dict]:
        return self._request("GET", "/devices") or []

    def create_command(self, device_id: str, command_type: str, idempotency_key: str,
                       parameters: dict | None = None) -> dict:
        return self._request("POST", f"/devices/{device_id}/commands", {
            "command_type": command_type, "parameters": parameters or {},
            "idempotency_key": idempotency_key, "source": "touchlessx"})

    def get_command(self, command_id: str) -> dict:
        return self._request("GET", f"/commands/{command_id}")

    def cancel_command(self, device_id: str, command_id: str) -> dict:
        return self._request("POST", f"/devices/{device_id}/commands/{command_id}/cancel")

    def capture_image(self, device_id: str, event_id: str) -> bytes:
        """The JPEG a snapshot command saved (kept on the server for a few days only)."""
        return self._request("GET", f"/devices/{device_id}/captures/{event_id}/image", raw=True)

    def ice_servers(self) -> list[str]:
        """STUN/TURN URLs for the live view's WebRTC connection."""
        body = self._request("GET", "/webrtc/ice-servers") or {}
        return [s["urls"] for s in body.get("ice_servers", []) if s.get("urls")]

    def photos(self, device_id: str) -> list[dict]:
        """Snapshots and person detections still on the server (it deletes them after its
        retention period), newest first."""
        return self._request("GET", f"/devices/{device_id}/events?image_only=true&limit=200") or []

    def videos(self, device_id: str) -> list[dict]:
        return self._request("GET", f"/devices/{device_id}/videos?limit=200") or []

    def video_file(self, device_id: str, video_id: str) -> bytes:
        return self._request("GET", f"/devices/{device_id}/videos/{video_id}/file", raw=True,
                             timeout=VIDEO_TIMEOUT)


def _detail(exc: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(exc.read() or b"{}").get("detail")
    except (ValueError, AttributeError):
        return ""
    return detail if isinstance(detail, str) else ""


class TokenStore:
    """The access token in the system credential store, per server address."""

    def __init__(self, backend=None) -> None:
        if backend is None:
            try:
                import keyring as backend  # type: ignore[no-redef]
            except ImportError:
                backend = None
        self._keyring = backend
        self._memory: dict[str, str] = {}

    @property
    def persistent(self) -> bool:
        return self._keyring is not None

    def load(self, server: str) -> str | None:
        if self._keyring is not None:
            try:
                return self._keyring.get_password(KEYRING_SERVICE, server)
            except Exception:
                return None
        return self._memory.get(server)

    def save(self, server: str, token: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.set_password(KEYRING_SERVICE, server, token)
                return
            except Exception:
                logger.warning("Could not save the CCTV session in the system credential store")
        self._memory[server] = token

    def clear(self, server: str) -> None:
        self._memory.pop(server, None)
        if self._keyring is not None:
            try:
                self._keyring.delete_password(KEYRING_SERVICE, server)
            except Exception:
                pass


@dataclass
class SentCommand:
    """One command as TouchlessX knows it, for the Home CCTV page."""
    name: str
    label: str
    device_id: str
    device_name: str
    idempotency_key: str
    status: str = "SENDING"
    command_id: str = ""
    error: str = ""
    sent_at: float = field(default_factory=time.time)
    follow_until: float = 0.0     # monotonic deadline for checking its outcome
    result: dict = field(default_factory=dict)   # what the camera reported, e.g. a snapshot's event_id


@dataclass
class Snapshot:
    """The latest photo a snapshot command took, for the Home CCTV page."""
    image: bytes
    device_name: str
    taken_at: float


@dataclass
class MediaItem:
    """One gallery entry: a snapshot, a person detection or a video clip."""
    kind: str                 # "snapshot" | "person" | "video"
    id: str
    device_id: str
    at: str                   # ISO time it was taken / the clip started
    duration: int = 0         # videos only, seconds
    image: bytes | None = None  # photos only, once downloaded


@dataclass
class Gallery:
    device_id: str = ""
    items: list[MediaItem] = field(default_factory=list)
    loading: bool = False
    message: str = ""
    loaded_at: float = 0.0


class CctvService:
    """Owns the client and does every network call on one background worker. The UI
    reads `snapshot()`; gestures call `send()`, which only queues work."""

    def __init__(self, server_url: str, token_store: TokenStore | None = None,
                 client_factory: Callable[..., CctvClient] = CctvClient, sleep=time.sleep) -> None:
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._client_factory = client_factory
        self._sleep = sleep
        self.tokens = token_store or TokenStore()
        self.server_url = ""
        self.client: CctvClient | None = None
        self.devices: list[dict] = []
        self.device_id = ""
        self.recent: list[SentCommand] = []
        self.message = ""
        self.error = ""               # the message, while it's a failure (shown in red)
        self.busy = False
        self.last_snapshot: Snapshot | None = None
        self.gallery = Gallery()
        self._photo_cache: dict[str, bytes] = {}  # event id -> JPEG, so a refresh re-downloads nothing
        self._following: list[SentCommand] = []  # sent, outcome not known yet
        self.set_server(server_url)
        threading.Thread(target=self._work, daemon=True, name="cctv").start()

    # ------------------------------------------------------------- state
    @property
    def signed_in(self) -> bool:
        return self.client is not None and bool(self.client.token)

    def set_server(self, server_url: str) -> None:
        try:
            url = validate_server_url(server_url)
        except CctvError as exc:
            self.client, self.message = None, str(exc)
            return
        self.server_url = url
        self.client = self._client_factory(url, token=self.tokens.load(url))

    def select_device(self, device_id: str) -> None:
        self.device_id = device_id

    def device_name(self, device_id: str) -> str:
        return next((d.get("device_name", "Camera") for d in self.devices if d.get("id") == device_id), "Camera")

    def snapshot(self) -> dict:
        with self._lock:
            return {"signed_in": self.signed_in, "devices": list(self.devices), "device_id": self.device_id,
                    "recent": [SentCommand(**vars(c)) for c in self.recent], "message": self.message,
                    "busy": self.busy, "server_url": self.server_url, "last_snapshot": self.last_snapshot,
                    "error": bool(self.error) and self.error == self.message}

    def live_session(self) -> tuple[str, str, str] | None:
        """(server address, access token, camera id) for the live view, or None when it
        can't connect yet. The token stays in memory; it's never written anywhere."""
        if not self.signed_in or not self.device_id:
            return None
        return self.server_url, self.client.token, self.device_id

    def ice_servers(self) -> list[str]:
        """Called from the live view's own thread, never the UI or tracking thread."""
        return self.client.ice_servers() if self.client is not None else []

    # ------------------------------------------------------------- gallery
    def gallery_snapshot(self) -> Gallery:
        with self._lock:
            g = self.gallery
            return Gallery(g.device_id, list(g.items), g.loading, g.message, g.loaded_at)

    def refresh_gallery(self) -> None:
        """Reloads the selected camera's photos and clips on a thread of its own, so a big
        download never holds up a gesture's command. One refresh at a time."""
        with self._lock:
            if self.gallery.loading:
                return
            device_id = self.device_id
            if not self.signed_in or not device_id:
                self.gallery = Gallery(message="Sign in and choose a camera to see its photos and clips")
                return
            if self.gallery.device_id != device_id:
                self.gallery = Gallery(device_id=device_id)
            self.gallery.loading = True
        threading.Thread(target=self._load_gallery, args=(device_id,), daemon=True, name="cctv-gallery").start()

    def _load_gallery(self, device_id: str) -> None:
        message = ""
        items: list[MediaItem] | None = None
        try:
            photos = self._with_retries(lambda: self.client.photos(device_id))
            videos = self._with_retries(lambda: self.client.videos(device_id))
            items = [MediaItem(PHOTO_KINDS.get(p.get("event_type"), "snapshot"), p["id"], device_id,
                               p.get("timestamp") or p.get("created_at") or "") for p in photos]
            items += [MediaItem("video", v["id"], device_id, v.get("started_at") or "",
                                int(v.get("duration_seconds") or 0)) for v in videos]
            items.sort(key=lambda item: item.at, reverse=True)
            for item in items:
                if item.kind == "video":
                    continue
                if item.id not in self._photo_cache:
                    try:
                        self._photo_cache[item.id] = self.client.capture_image(device_id, item.id)
                    except CctvError as exc:
                        if exc.kind != "http":  # 404: deleted meanwhile - just leave it out
                            message = f"Some photos couldn't be downloaded: {exc}"
                        continue
                item.image = self._photo_cache[item.id]
            items = [i for i in items if i.kind == "video" or i.image is not None]
            alive = {i.id for i in items}
            for gone in [k for k in list(self._photo_cache) if k not in alive]:  # copy: the worker adds too
                del self._photo_cache[gone]  # deleted on the server after its retention period
        except CctvError as exc:
            message = str(exc)
            if exc.kind == "auth":
                self._fail(exc)
        except Exception:
            logger.exception("Loading the CCTV gallery failed")
            message = "Something went wrong loading the gallery"
        with self._lock:
            if self.gallery.device_id != device_id:  # another camera was chosen meanwhile
                self.gallery.loading = False
                return
            if items is not None:
                self.gallery.items, self.gallery.loaded_at = items, time.time()
            self.gallery.loading, self.gallery.message = False, message

    def download_video(self, item: MediaItem, folder: str) -> str:
        """Saves a clip to `folder` and returns its path. Blocking: call off the UI thread."""
        data = self._with_retries(lambda: self.client.video_file(item.device_id, item.id))
        path = f"{folder.rstrip('/')}/cctv-{item.id}.mp4"
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    # ------------------------------------------------------------- requests (queued)
    def sign_in(self, email: str, password: str) -> None:
        self._jobs.put(("sign_in", email, password))

    def sign_out(self) -> None:
        if self.server_url:
            self.tokens.clear(self.server_url)
        if self.client is not None:
            self.client.token = None
        with self._lock:
            self.devices, self.message = [], "Signed out"
            self.gallery, self.last_snapshot = Gallery(), None
            self._photo_cache.clear()

    def refresh_devices(self) -> None:
        self._jobs.put(("devices",))

    def send(self, name: str) -> bool:
        """Queues a camera command for the selected camera. Returns False if it can't be
        sent at all (unknown command, not signed in, no camera chosen)."""
        spec = CCTV_COMMANDS.get(name)
        if spec is None:
            return False
        if not self.signed_in or not self.device_id:
            with self._lock:
                self.message = "Sign in and choose a camera on the Home CCTV page first"
            return False
        sent = SentCommand(name, spec.label, self.device_id, self.device_name(self.device_id), uuid.uuid4().hex)
        with self._lock:
            self.recent.insert(0, sent)
            del self.recent[RECENT_LIMIT:]
        self._jobs.put(("command", sent, spec.api_type))
        return True

    def cancel(self, command_id: str) -> None:
        self._jobs.put(("cancel", command_id))

    # ------------------------------------------------------------- worker
    def _work(self) -> None:
        """Runs queued requests as they come, and between them checks on commands that
        are still running - so a slow camera never holds up the next gesture."""
        while True:
            try:
                job = self._jobs.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                self._follow()
                continue
            with self._lock:
                self.busy = True
            try:
                getattr(self, "_do_" + job[0])(*job[1:])
            except CctvError as exc:
                self._fail(exc)
            except Exception:
                logger.exception("CCTV request failed")
                with self._lock:
                    self.message = "Something went wrong talking to the CCTV server"
            finally:
                with self._lock:
                    self.busy = False

    def _fail(self, exc: CctvError) -> None:
        if exc.kind == "auth" and exc.status == 401 and self.server_url:
            self.tokens.clear(self.server_url)
            if self.client is not None:
                self.client.token = None
        with self._lock:
            self.message = str(exc)
            self.error = self.message

    def _do_sign_in(self, email: str, password: str) -> None:
        if self.client is None:
            raise CctvError("config", self.message or "Set the server address first")
        token = self.client.login(email, password)
        self.tokens.save(self.server_url, token)
        with self._lock:
            self.message = "Signed in"
        self._do_devices()

    def _do_devices(self) -> None:
        if self.client is None:
            raise CctvError("config", "Set the server address first")
        devices = self.client.devices()
        with self._lock:
            self.devices = devices
            if self.device_id not in {d.get("id") for d in devices}:
                self.device_id = devices[0]["id"] if devices else ""
            self.message = f"{len(devices)} camera(s)"

    def _with_retries(self, call):
        for attempt in range(NETWORK_ATTEMPTS):
            try:
                return call()
            except CctvError as exc:
                if exc.kind != "network" or attempt == NETWORK_ATTEMPTS - 1:
                    raise
                self._sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

    def _do_command(self, sent: SentCommand, api_type: str) -> None:
        try:
            body = self._with_retries(
                lambda: self.client.create_command(sent.device_id, api_type, sent.idempotency_key))
        except CctvError as exc:
            self._update(sent, status="FAILED", error=str(exc))
            if exc.kind == "auth":
                self._fail(exc)
            return
        self._update(sent, status=body["status"], command_id=body["id"],
                     follow_until=time.monotonic() + FOLLOW_SECONDS)
        if sent.status not in TERMINAL:
            self._following.append(sent)
        else:
            self._finished(sent, body)

    def _follow(self) -> None:
        """One check of every command whose outcome isn't known yet."""
        if self.client is None:
            return
        for sent in list(self._following):
            try:
                body = self.client.get_command(sent.command_id)
            except CctvError as exc:
                if exc.kind == "network":
                    body = None  # keep following; the command itself is safe on the server
                else:
                    self._update(sent, error=str(exc))
                    self._following.remove(sent)
                    continue
            if body is not None:
                self._update(sent, status=body["status"], error=body.get("error_code") or "")
            if sent.status in TERMINAL or time.monotonic() > sent.follow_until:
                self._following.remove(sent)
                if body is not None and sent.status in TERMINAL:
                    self._finished(sent, body)

    def _finished(self, sent: SentCommand, body: dict) -> None:
        """A command reached its outcome. A successful snapshot's photo is fetched so the
        page can show it; if that fails the command still succeeded - only the preview is
        missing, which the message says."""
        self._update(sent, result=body.get("result") or {})
        event_id = sent.result.get("event_id")
        if sent.status != "SUCCEEDED" or sent.name != "capture_snapshot" or not event_id:
            return
        try:
            image = self._with_retries(lambda: self.client.capture_image(sent.device_id, event_id))
        except CctvError as exc:
            with self._lock:
                self.message = f"Snapshot taken, but it couldn't be downloaded: {exc}"
            return
        with self._lock:
            self.last_snapshot = Snapshot(image, sent.device_name, time.time())
            self._photo_cache[event_id] = image
        if sent.device_id == self.device_id:
            self.refresh_gallery()  # the new photo shows up there too

    def _do_cancel(self, command_id: str) -> None:
        sent = next((c for c in self.recent if c.command_id == command_id), None)
        if sent is None or self.client is None:
            return
        body = self.client.cancel_command(sent.device_id, command_id)
        self._update(sent, status=body["status"])

    def _update(self, sent: SentCommand, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(sent, key, value)

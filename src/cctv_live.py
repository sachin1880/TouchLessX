"""Live video from a HomeSmartCCTV camera, received over WebRTC exactly the way the
phone's viewer app does it: join the backend's signaling relay as the "viewer", send a
receive-only offer when the camera is there, and decode the frames that arrive.

* Everything runs on one background thread with its own asyncio loop - never the UI or
  tracking thread. The page only reads `status()` and `frame()`.
* Video goes straight from the phone to this computer; the backend only relays the
  offer, answer and network candidates. Only video is requested, never audio.
* The backend allows one viewer per camera: opening the live view here closes it in the
  phone viewer app, and the other way round (shown as "replaced").
* The access token travels in the signaling URL (WebSockets can't carry headers here);
  that URL is never logged.
* `aiortc` and `websockets` are optional: without them the rest of Home CCTV works and
  the live view says what to install.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Callable
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 8.0
MAX_WIDTH = 960               # frames are scaled down to this for the page
MIN_FRAME_GAP = 1 / 15        # at most ~15 converted frames a second; the rest are only decoded

# status -> what the page shows
IDLE, CONNECTING, WAITING, NEGOTIATING, LIVE, REPLACED, ENDED, FAILED = (
    "idle", "connecting", "waiting", "negotiating", "live", "replaced", "ended", "failed")


def signaling_url(server_url: str, token: str, device_id: str) -> str:
    """ws(s)://host/api/devices/<id>/signal?token=…&role=viewer from the API address."""
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base = f"{scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return f"{base}/devices/{quote(device_id, safe='')}/signal?token={quote(token, safe='')}&role=viewer"


def parse_candidate(message: dict):
    """An aiortc candidate from a trickled "ice-candidate" message, or None for the
    end-of-candidates marker or anything unreadable."""
    from aiortc.sdp import candidate_from_sdp

    text = (message.get("candidate") or "").strip()
    if not text:
        return None
    if text.startswith("candidate:"):
        text = text[len("candidate:"):]
    try:
        candidate = candidate_from_sdp(text)
    except (ValueError, IndexError, AssertionError):  # aiortc asserts on malformed candidates
        return None
    candidate.sdpMid = message.get("sdpMid")
    candidate.sdpMLineIndex = message.get("sdpMLineIndex")
    return candidate


class LiveView:
    """One live view at a time. `start()` replaces any running one; `stop()` ends it."""

    def __init__(self, ice_servers: Callable[[], list[str]] = lambda: [], connect=None) -> None:
        self._ice_servers = ice_servers
        self._connect = connect        # websockets.connect, replaceable in tests
        self._lock = threading.Lock()
        self._generation = 0           # bumps on every start/stop; older sessions go quiet
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._status, self._message = IDLE, ""
        self._frame = None             # latest PIL image
        self._frame_seq = 0
        self._device_id = ""

    # ------------------------------------------------------------- read by the page
    def status(self) -> tuple[str, str]:
        with self._lock:
            return self._status, self._message

    def frame(self) -> tuple[int, object]:
        """(sequence number, latest frame as a PIL image or None). The number changes
        with every new frame, so the page redraws only when there is something new."""
        with self._lock:
            return self._frame_seq, self._frame

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def running(self) -> bool:
        return self.status()[0] in (CONNECTING, WAITING, NEGOTIATING, LIVE)

    # ------------------------------------------------------------- control
    def start(self, server_url: str, token: str, device_id: str) -> None:
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._status, self._message = CONNECTING, "Connecting…"
            self._frame, self._device_id = None, device_id
        url = signaling_url(server_url, token, device_id)
        threading.Thread(target=self._thread, args=(generation, url), daemon=True, name="cctv-live").start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            loop, task = self._loop, self._task
            self._loop = self._task = None
            if self._status in (CONNECTING, WAITING, NEGOTIATING, LIVE):
                self._status, self._message = IDLE, ""
            self._frame = None
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)

    # ------------------------------------------------------------- session
    def _set(self, generation: int, status: str, message: str = "") -> None:
        with self._lock:
            if generation == self._generation:
                self._status, self._message = status, message

    def _thread(self, generation: int, url: str) -> None:
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(self._session(generation, url))
            with self._lock:
                if generation != self._generation:
                    task.cancel()
                else:
                    self._loop, self._task = loop, task
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("CCTV live view stopped unexpectedly")
            self._set(generation, FAILED, "The live view stopped unexpectedly")
        finally:
            loop.close()

    async def _session(self, generation: int, url: str) -> None:
        try:
            from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
            connect = self._connect
            if connect is None:
                from websockets.asyncio.client import connect
        except ImportError:
            self._set(generation, FAILED, "Live view needs aiortc and websockets: pip install -r requirements.txt")
            return

        try:
            urls = await asyncio.to_thread(self._ice_servers)
        except Exception:
            urls = []  # a local camera connects without STUN; don't block the view on it
        config = RTCConfiguration(iceServers=[RTCIceServer(urls=u) for u in urls])

        pc = None
        pending: list = []  # candidates that arrived before the answer

        async def close_pc():
            nonlocal pc
            if pc is not None:
                await pc.close()
                pc = None
            pending.clear()

        async def offer(ws):
            nonlocal pc
            await close_pc()
            pc = RTCPeerConnection(config)
            current = pc

            @current.on("track")
            def on_track(track):
                if track.kind == "video":
                    asyncio.ensure_future(self._frames(generation, track))

            @current.on("connectionstatechange")
            async def on_state():
                if current is not pc:
                    return
                if current.connectionState == "connected":
                    self._set(generation, LIVE)
                elif current.connectionState == "failed":
                    self._set(generation, FAILED, "Couldn't connect to the camera's video")

            current.addTransceiver("video", direction="recvonly")
            await current.setLocalDescription(await current.createOffer())  # gathers all our candidates
            await ws.send(json.dumps({"type": "offer", "sdp": current.localDescription.sdp}))
            self._set(generation, NEGOTIATING, "Connecting to the camera…")

        try:
            async with connect(url, open_timeout=CONNECT_TIMEOUT, max_size=2 ** 20) as ws:
                self._set(generation, WAITING, "Waiting for the camera - start it on the phone")
                async for raw in ws:
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    kind = message.get("type")
                    if kind == "peer-joined" and message.get("role") == "camera":
                        await offer(ws)
                    elif kind == "answer" and pc is not None:
                        await pc.setRemoteDescription(RTCSessionDescription(message.get("sdp", ""), "answer"))
                        for candidate in pending:
                            await pc.addIceCandidate(candidate)
                        pending.clear()
                    elif kind == "ice-candidate" and pc is not None:
                        candidate = parse_candidate(message)
                        if candidate is None:
                            continue
                        if pc.remoteDescription is None:
                            pending.append(candidate)
                        else:
                            await pc.addIceCandidate(candidate)
                    elif kind == "peer-left" and message.get("role") == "camera":
                        await close_pc()
                        with self._lock:
                            if generation == self._generation:
                                self._frame = None
                        self._set(generation, WAITING, "The camera stopped streaming - waiting for it")
                    elif kind == "replaced":
                        self._set(generation, REPLACED, "Opened on another viewer, so it closed here")
                        return
                self._set(generation, ENDED, "Disconnected from the CCTV server")
        except asyncio.CancelledError:
            raise
        except OSError:
            self._set(generation, FAILED, "Can't reach the CCTV server")
        except Exception as exc:
            refused = getattr(getattr(exc, "response", None), "status_code", None)
            if refused:
                self._set(generation, FAILED, "The server refused the live view - sign in again")
            else:
                logger.warning("CCTV live view failed: %s", type(exc).__name__)
                self._set(generation, FAILED, "The live view connection failed")
        finally:
            await close_pc()

    async def _frames(self, generation: int, track) -> None:
        """Receives every frame (so the decoder keeps up) and keeps a scaled copy of at
        most ~15 a second for the page."""
        last = 0.0
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return  # the track ended: the camera left or the view stopped
            now = time.monotonic()
            if now - last < MIN_FRAME_GAP:
                continue
            last = now
            width, height = frame.width, frame.height
            if width > MAX_WIDTH:
                width, height = MAX_WIDTH, round(height * MAX_WIDTH / width / 2) * 2
            image = frame.reformat(width=width, height=height, format="rgb24").to_image()
            with self._lock:
                if generation != self._generation:
                    return
                self._frame, self._frame_seq = image, self._frame_seq + 1

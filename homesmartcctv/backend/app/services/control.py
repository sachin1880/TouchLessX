"""In-memory registry of camera device-control connections.

Separate from the signaling relay (signaling.py) on purpose: signaling exists only
while a viewer is watching, and carries WebRTC negotiation between two peers; the
control channel is open whenever the camera app is running and carries commands one
way, backend -> camera. The camera answers over the REST ack / result endpoints, so
every state change goes through the same authorization and audit path.

A push is only a fast path. If no control connection is open, or the push is lost,
the camera still finds the command by polling `/commands/pending`. State lives in
process memory, like signaling - correct for one backend process.
"""

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ControlManager:
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._sockets[device_id].add(websocket)

    async def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._sockets.get(device_id)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    self._sockets.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return bool(self._sockets.get(device_id))

    async def push(self, device_id: str, message: dict) -> bool:
        """Sends to every control connection of the device. True if at least one took it."""
        delivered = False
        for websocket in list(self._sockets.get(device_id, ())):
            try:
                await websocket.send_json(message)
                delivered = True
            except Exception:
                logger.debug("Control push failed; the camera will pick the command up by polling")
        return delivered


control_manager = ControlManager()

"""In-memory WebRTC signaling relay.

The backend never sees video - it only relays SDP offers/answers and ICE
candidates between exactly one camera connection and one viewer connection
per device, scoped by device_id. V1 supports a single simultaneous viewer
per device (a second viewer connecting replaces the first); multi-viewer
fan-out is future work, not needed for a personal camera.

State lives in process memory, which is correct for V1 (a single backend
instance). Scaling to multiple backend instances later would need a shared
pub/sub (e.g. Redis) - noted here so a future rewrite isn't a surprise.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

Role = Literal["camera", "viewer"]


@dataclass
class DeviceRoom:
    camera: Optional[WebSocket] = None
    viewer: Optional[WebSocket] = None


class SignalingManager:
    def __init__(self) -> None:
        self._rooms: dict[str, DeviceRoom] = {}
        self._lock = asyncio.Lock()

    async def connect(self, device_id: str, role: Role, websocket: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.setdefault(device_id, DeviceRoom())
            previous = getattr(room, role)
            setattr(room, role, websocket)

        if previous is not None:
            await self._safe_send(previous, {"type": "replaced"})
            await self._safe_close(previous)

        peer = self._peer(device_id, role)
        if peer is not None:
            await self._safe_send(peer, {"type": "peer-joined", "role": role})
            await self._safe_send(websocket, {"type": "peer-joined", "role": self._other_role(role)})

    async def disconnect(self, device_id: str, role: Role, websocket: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.get(device_id)
            if room is None:
                return
            if getattr(room, role) is websocket:
                setattr(room, role, None)
            if room.camera is None and room.viewer is None:
                self._rooms.pop(device_id, None)

        peer = self._peer(device_id, role)
        if peer is not None:
            await self._safe_send(peer, {"type": "peer-left", "role": role})

    async def relay(self, device_id: str, role: Role, message: dict) -> None:
        peer = self._peer(device_id, role)
        if peer is not None:
            await self._safe_send(peer, message)

    def _peer(self, device_id: str, role: Role) -> Optional[WebSocket]:
        room = self._rooms.get(device_id)
        if room is None:
            return None
        return room.viewer if role == "camera" else room.camera

    @staticmethod
    def _other_role(role: Role) -> Role:
        return "viewer" if role == "camera" else "camera"

    @staticmethod
    async def _safe_send(websocket: WebSocket, message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            logger.debug("Failed to deliver signaling message; peer likely disconnected")

    @staticmethod
    async def _safe_close(websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except Exception:
            pass


signaling_manager = SignalingManager()

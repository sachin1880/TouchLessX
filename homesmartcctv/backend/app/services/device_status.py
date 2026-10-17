"""Device online/offline detection and camera-event creation.

A device can only send a heartbeat while it has connectivity, so the backend
can never be *told* the instant a device goes offline - it can only notice
staleness. We treat a device as ONLINE if `last_seen` is within
`heartbeat_offline_threshold_seconds`, and re-check that on every read
(list/get device, heartbeat). When the computed state flips, we persist it
and record exactly one DEVICE_ONLINE / DEVICE_OFFLINE transition event -
never one per request - so reconnect loops don't flood the event history.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.camera_device import CameraDevice, DeviceStatus
from app.models.camera_event import CameraEvent, EventType
from app.schemas.device import HeartbeatRequest

logger = logging.getLogger(__name__)
settings = get_settings()


def create_event(
    db: Session,
    device_id: UUID,
    event_type: EventType,
    metadata: Optional[dict[str, Any]] = None,
) -> CameraEvent:
    event = CameraEvent(camera_device_id=device_id, event_type=event_type, event_metadata=metadata)
    db.add(event)
    db.commit()
    logger.info("Camera event recorded: device_id=%s event_type=%s", device_id, event_type.value)
    return event


def _is_within_heartbeat_window(last_seen: Optional[datetime]) -> bool:
    if last_seen is None:
        return False
    now = datetime.now(timezone.utc)
    return (now - last_seen).total_seconds() <= settings.heartbeat_offline_threshold_seconds


def sync_online_status(db: Session, device: CameraDevice, persist: bool = True) -> bool:
    online = _is_within_heartbeat_window(device.last_seen)
    was_online = device.status == DeviceStatus.ONLINE

    if online != was_online:
        device.status = DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE
        if persist:
            db.add(device)
            db.commit()
            db.refresh(device)
        if not online:
            create_event(db, device.id, EventType.DEVICE_OFFLINE)

    return online


def apply_heartbeat(db: Session, device: CameraDevice, payload: HeartbeatRequest) -> CameraDevice:
    was_online = _is_within_heartbeat_window(device.last_seen)
    previous_network_status = device.network_status
    previous_battery = device.battery_level

    device.last_seen = datetime.now(timezone.utc)
    device.status = DeviceStatus.ONLINE
    if payload.battery_level is not None:
        device.battery_level = payload.battery_level
    if payload.network_status is not None:
        device.network_status = payload.network_status
    if payload.camera_status is not None:
        device.camera_status = payload.camera_status

    db.add(device)
    db.commit()
    db.refresh(device)

    if not was_online:
        create_event(db, device.id, EventType.DEVICE_ONLINE)

    if (
        previous_network_status is not None
        and payload.network_status is not None
        and previous_network_status != payload.network_status
    ):
        if payload.network_status.value == "DISCONNECTED":
            create_event(db, device.id, EventType.NETWORK_DISCONNECTED)
        elif previous_network_status.value == "DISCONNECTED":
            create_event(db, device.id, EventType.NETWORK_RECONNECTED)

    if payload.battery_level is not None:
        threshold = settings.low_battery_threshold_percent
        crossed_below = (previous_battery is None or previous_battery >= threshold) and payload.battery_level < threshold
        if crossed_below:
            create_event(db, device.id, EventType.LOW_BATTERY, metadata={"battery_level": payload.battery_level})

    return device

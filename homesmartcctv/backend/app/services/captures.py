"""Event-capture snapshot storage.

Deliberately not a video-storage service: this backend never records continuously.
It only stores the still images a viewer explicitly captures, and only for
`capture_retention_days` (default 3) before deleting both the file and its event
record for good. Cleanup runs at startup and opportunistically on every new
capture - no scheduler/cron dependency, appropriate for a personal single-camera
app rather than a service with reliably frequent traffic.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.camera_event import CameraEvent, EventType

logger = logging.getLogger(__name__)
settings = get_settings()

# Event types that have a JPEG saved under captures/ and are therefore subject to the same
# retention window - a viewer-triggered manual capture and an auto-detected person snapshot
# are stored identically, just with a different event_type.
IMAGE_BACKED_EVENT_TYPES = (EventType.MANUAL_CAPTURE, EventType.PERSON_DETECTED)


def capture_file_path(device_id: UUID, event_id: UUID) -> Path:
    return Path(settings.media_root) / "captures" / str(device_id) / f"{event_id}.jpg"


def save_capture(device_id: UUID, event_id: UUID, content: bytes) -> Path:
    path = capture_file_path(device_id, event_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def cleanup_expired_captures(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.capture_retention_days)
    expired = (
        db.query(CameraEvent)
        .filter(CameraEvent.event_type.in_(IMAGE_BACKED_EVENT_TYPES), CameraEvent.created_at < cutoff)
        .all()
    )

    for event in expired:
        path = capture_file_path(event.camera_device_id, event.id)
        path.unlink(missing_ok=True)
        db.delete(event)

    if expired:
        db.commit()
        logger.info("Removed %d expired capture(s)", len(expired))

    return len(expired)

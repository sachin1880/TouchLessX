"""Continuous-recording segment storage.

The camera uploads one ~60s clip at a time while streaming rather than one long file -
simpler on both ends (bounded upload size, no resumable-upload handling) and it makes
retention trivial: delete whole segments older than `video_retention_days`, the same
pattern used for capture() snapshots. Cleanup runs at startup and opportunistically on
every new segment upload - no scheduler/cron dependency.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.camera_video import CameraVideo

logger = logging.getLogger(__name__)
settings = get_settings()


def video_file_path(device_id: UUID, video_id: UUID) -> Path:
    return Path(settings.media_root) / "videos" / str(device_id) / f"{video_id}.mp4"


def save_video(device_id: UUID, video_id: UUID, content: bytes) -> Path:
    path = video_file_path(device_id, video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def cleanup_expired_videos(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.video_retention_days)
    expired = db.query(CameraVideo).filter(CameraVideo.created_at < cutoff).all()

    for video in expired:
        path = video_file_path(video.camera_device_id, video.id)
        path.unlink(missing_ok=True)
        db.delete(video)

    if expired:
        db.commit()
        logger.info("Removed %d expired video segment(s)", len(expired))

    return len(expired)

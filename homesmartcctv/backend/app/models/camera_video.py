import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.camera_device import CameraDevice


class CameraVideo(Base):
    """One uploaded recording segment (~60s) of a camera's continuous stream.

    Segments are produced and uploaded one at a time while the camera is streaming -
    there's no single long-running recording, just a sequence of short clips, which keeps
    both the mobile upload path and backend storage/retention simple (delete rows older
    than `video_retention_days`, same pattern as capture() events).
    """

    __tablename__ = "camera_videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    device: Mapped["CameraDevice"] = relationship(back_populates="videos")

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.camera_device import CameraDevice


class EventType(str, enum.Enum):
    CAMERA_STARTED = "CAMERA_STARTED"
    CAMERA_STOPPED = "CAMERA_STOPPED"
    DEVICE_ONLINE = "DEVICE_ONLINE"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    NETWORK_DISCONNECTED = "NETWORK_DISCONNECTED"
    NETWORK_RECONNECTED = "NETWORK_RECONNECTED"
    LOW_BATTERY = "LOW_BATTERY"
    MANUAL_CAPTURE = "MANUAL_CAPTURE"
    PERSON_DETECTED = "PERSON_DETECTED"


class CameraEvent(Base):
    __tablename__ = "camera_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"), nullable=False)
    # Python attr renamed from `metadata` - that name is reserved by SQLAlchemy's Declarative base.
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["CameraDevice"] = relationship(back_populates="events")

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.camera_event import CameraEvent
    from app.models.camera_video import CameraVideo
    from app.models.user import User


class DevicePlatform(str, enum.Enum):
    ANDROID = "ANDROID"
    IOS = "IOS"


class DeviceStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class NetworkStatus(str, enum.Enum):
    WIFI = "WIFI"
    CELLULAR = "CELLULAR"
    DISCONNECTED = "DISCONNECTED"


class CameraStatus(str, enum.Enum):
    IDLE = "IDLE"
    STREAMING = "STREAMING"


class CameraDevice(Base):
    __tablename__ = "camera_devices"
    # One phone can be a camera on several accounts (one registration per account);
    # within one account it is registered once.
    __table_args__ = (UniqueConstraint("user_id", "device_uuid", name="uq_camera_devices_user_device_uuid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_name: Mapped[str] = mapped_column(String(120), nullable=False)
    device_uuid: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    platform: Mapped[DevicePlatform] = mapped_column(Enum(DevicePlatform, name="device_platform"), nullable=False)
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="device_status"), default=DeviceStatus.OFFLINE, nullable=False
    )
    camera_status: Mapped[CameraStatus] = mapped_column(
        Enum(CameraStatus, name="camera_status"), default=CameraStatus.IDLE, nullable=False
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    battery_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    network_status: Mapped[Optional[NetworkStatus]] = mapped_column(
        Enum(NetworkStatus, name="network_status"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="devices")
    events: Mapped[list["CameraEvent"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="CameraEvent.timestamp.desc()",
    )
    videos: Mapped[list["CameraVideo"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="CameraVideo.started_at.desc()",
    )

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.camera_device import CameraDevice


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CommandType(str, enum.Enum):
    """The complete allowlist of what a camera can be asked to do. Anything else is
    rejected at the API - there is no free-form or shell-style command."""

    REFRESH_STATUS = "REFRESH_STATUS"
    SWITCH_CAMERA = "SWITCH_CAMERA"
    CAPTURE_SNAPSHOT = "CAPTURE_SNAPSHOT"


class CommandStatus(str, enum.Enum):
    PENDING = "PENDING"            # stored, not yet handed to the camera
    DELIVERED = "DELIVERED"        # handed to the camera (pushed or polled)
    ACKNOWLEDGED = "ACKNOWLEDGED"  # the camera accepted it and is carrying it out
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"            # not acknowledged before expires_at
    CANCELLED = "CANCELLED"        # withdrawn by the user before the camera accepted it


TERMINAL_STATUSES = frozenset(
    {CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.EXPIRED, CommandStatus.CANCELLED}
)


class DeviceCommand(Base):
    """One request for a camera to do something, from creation to its final outcome.

    Commands are only ever created by the device's owner and carried out by that
    device. `idempotency_key` makes a retried request (a double gesture, a network
    retry) return the original command instead of creating a second one.
    """

    __tablename__ = "device_commands"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_device_commands_user_idempotency_key"),
        Index("ix_device_commands_device_status", "camera_device_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_devices.id", ondelete="CASCADE"), nullable=False
    )
    command_type: Mapped[CommandType] = mapped_column(Enum(CommandType, name="command_type"), nullable=False)
    parameters: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[CommandStatus] = mapped_column(
        Enum(CommandStatus, name="command_status"), default=CommandStatus.PENDING, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Where the request came from ("touchlessx", "viewer", ...) - informational only.
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Set in Python so commands created in one transaction still sort in order (see audit_log.py).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["CameraDevice"] = relationship()

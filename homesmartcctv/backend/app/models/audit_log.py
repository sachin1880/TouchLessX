import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """An append-only record of who did what to which device, and how it ended.

    Rows outlive the things they mention (foreign keys are SET NULL), so deleting a
    device or a command never erases its history. `details` holds only small,
    sanitized values - never tokens, passwords or media.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # "user" (a person through an app) or "device" (the camera reporting back).
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    camera_device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    command_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device_commands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Set in Python, not by Postgres' now(): now() is the transaction's start time, so
    # entries written in one transaction would tie and lose their order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False, index=True
    )

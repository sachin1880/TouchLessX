"""Device command lifecycle.

    PENDING ──► DELIVERED ──► ACKNOWLEDGED ──► SUCCEEDED | FAILED
       │            │
       └────────────┴──► EXPIRED | CANCELLED

* Only the device's owner creates, reads and cancels its commands; only that device
  acknowledges them and reports results (same account today - see SECURITY notes).
* Terminal states are final. Repeating a transition that already happened (a retried
  ack or result) returns the command unchanged; any other invalid transition is a 409.
* Expiry is applied lazily on every read and write, like the online/offline status
  in device_status.py, so no background job is needed: a command the camera never
  accepted becomes EXPIRED at `expires_at`, and one it accepted but never finished
  becomes FAILED ("no_result") after the grace period.
* Every change is written to the audit log in the same transaction.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.camera_device import CameraDevice
from app.models.device_command import TERMINAL_STATUSES, CommandStatus, DeviceCommand
from app.models.user import User
from app.schemas.command import PARAMETER_SCHEMAS, CommandCreate, CommandResultReport
from app.services import audit

logger = logging.getLogger(__name__)
settings = get_settings()

OPEN_STATUSES = (CommandStatus.PENDING, CommandStatus.DELIVERED, CommandStatus.ACKNOWLEDGED)
NOT_ACCEPTED = (CommandStatus.PENDING, CommandStatus.DELIVERED)


class CommandError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _finish(db: Session, command: DeviceCommand, status: CommandStatus, error_code: Optional[str],
            actor_type: str, action: str) -> None:
    command.status = status
    command.error_code = error_code
    command.completed_at = _now()
    audit.record(db, action=action, outcome=status.value.lower(), actor_type=actor_type,
                 user_id=command.user_id, device_id=command.camera_device_id, command_id=command.id,
                 details={"command_type": command.command_type.value, "error_code": error_code})


def expire_overdue(db: Session, device_id: uuid.UUID) -> int:
    """Settles this device's commands that ran out of time. Returns how many changed."""
    now = _now()
    grace = timedelta(seconds=settings.command_result_grace_seconds)
    changed = 0
    open_commands = (
        db.query(DeviceCommand)
        .filter(DeviceCommand.camera_device_id == device_id, DeviceCommand.status.in_(OPEN_STATUSES))
        .all()
    )
    for command in open_commands:
        if command.status in NOT_ACCEPTED and now >= command.expires_at:
            _finish(db, command, CommandStatus.EXPIRED, "expired", "system", "command.expired")
            changed += 1
        elif command.status == CommandStatus.ACKNOWLEDGED and now >= command.expires_at + grace:
            _finish(db, command, CommandStatus.FAILED, "no_result", "system", "command.timed_out")
            changed += 1
    if changed:
        db.commit()
    return changed


def _normalized_parameters(payload: CommandCreate) -> dict:
    schema = PARAMETER_SCHEMAS.get(payload.command_type)
    if schema is None:
        raise CommandError(422, f"Command type {payload.command_type.value} is not supported")
    try:
        return schema.model_validate(payload.parameters).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise CommandError(422, f"Invalid parameters: {exc.errors()[0]['msg']}") from exc


def create_command(db: Session, user: User, device: CameraDevice, payload: CommandCreate) -> tuple[DeviceCommand, bool]:
    """Returns (command, created). A repeated idempotency key returns the original
    command (created=False) if it asked for the same thing, and is a 409 otherwise."""
    parameters = _normalized_parameters(payload)

    existing = (
        db.query(DeviceCommand)
        .filter(DeviceCommand.user_id == user.id, DeviceCommand.idempotency_key == payload.idempotency_key)
        .first()
    )
    if existing is not None:
        same = (existing.camera_device_id == device.id and existing.command_type == payload.command_type
                and (existing.parameters or {}) == parameters)
        if not same:
            raise CommandError(409, "This idempotency_key was already used for a different command")
        expire_overdue(db, device.id)
        db.refresh(existing)
        return existing, False

    ttl = payload.ttl_seconds or settings.command_default_ttl_seconds
    if ttl > settings.command_max_ttl_seconds:
        raise CommandError(422, f"ttl_seconds must be at most {settings.command_max_ttl_seconds}")

    expire_overdue(db, device.id)
    open_count = (
        db.query(DeviceCommand)
        .filter(DeviceCommand.camera_device_id == device.id, DeviceCommand.status.in_(OPEN_STATUSES))
        .count()
    )
    if open_count >= settings.command_max_open_per_device:
        audit.record(db, action="command.rejected", outcome="too_many_open", user_id=user.id, device_id=device.id,
                     details={"command_type": payload.command_type.value})
        db.commit()
        raise CommandError(429, "Too many unfinished commands for this camera; wait for them to finish or expire")

    command = DeviceCommand(
        user_id=user.id,
        camera_device_id=device.id,
        command_type=payload.command_type,
        parameters=parameters or None,
        idempotency_key=payload.idempotency_key,
        source=payload.source,
        expires_at=_now() + timedelta(seconds=ttl),
    )
    db.add(command)
    try:
        db.flush()
    except IntegrityError:
        # The same key arrived twice at the same moment: the other request won.
        db.rollback()
        return create_command(db, user, device, payload)
    audit.record(db, action="command.created", outcome="pending", user_id=user.id, device_id=device.id,
                 command_id=command.id, details={"command_type": command.command_type.value, "source": payload.source})
    db.commit()
    db.refresh(command)
    logger.info("Command created: command_id=%s device_id=%s type=%s", command.id, device.id, command.command_type.value)
    return command, True


def get_owned_command(db: Session, user: User, command_id: uuid.UUID) -> DeviceCommand:
    command = db.get(DeviceCommand, command_id)
    if command is None or command.user_id != user.id:
        # Someone else's command looks exactly like a missing one.
        raise CommandError(404, "Command not found")
    expire_overdue(db, command.camera_device_id)
    db.refresh(command)
    return command


def get_device_command(db: Session, device: CameraDevice, command_id: uuid.UUID) -> DeviceCommand:
    command = db.get(DeviceCommand, command_id)
    if command is None or command.camera_device_id != device.id:
        raise CommandError(404, "Command not found")
    expire_overdue(db, device.id)
    db.refresh(command)
    return command


def mark_delivered(db: Session, commands: list[DeviceCommand]) -> None:
    now = _now()
    for command in commands:
        if command.status == CommandStatus.PENDING:
            command.status = CommandStatus.DELIVERED
            command.delivered_at = now
    db.commit()


def take_pending(db: Session, device: CameraDevice) -> list[DeviceCommand]:
    """The commands the camera hasn't accepted yet, oldest first; marks them delivered."""
    expire_overdue(db, device.id)
    commands = (
        db.query(DeviceCommand)
        .filter(DeviceCommand.camera_device_id == device.id, DeviceCommand.status.in_(NOT_ACCEPTED))
        .order_by(DeviceCommand.created_at)
        .all()
    )
    mark_delivered(db, commands)
    return commands


def acknowledge(db: Session, device: CameraDevice, command_id: uuid.UUID) -> DeviceCommand:
    command = get_device_command(db, device, command_id)
    if command.status == CommandStatus.ACKNOWLEDGED:
        return command  # a retried ack
    if command.status not in NOT_ACCEPTED:
        raise CommandError(409, f"Command is already {command.status.value.lower()}")
    command.status = CommandStatus.ACKNOWLEDGED
    command.acknowledged_at = _now()
    if command.delivered_at is None:
        command.delivered_at = command.acknowledged_at
    audit.record(db, action="command.acknowledged", outcome="acknowledged", actor_type="device",
                 user_id=command.user_id, device_id=device.id, command_id=command.id,
                 details={"command_type": command.command_type.value})
    db.commit()
    db.refresh(command)
    return command


def report_result(db: Session, device: CameraDevice, command_id: uuid.UUID, report: CommandResultReport) -> DeviceCommand:
    command = get_device_command(db, device, command_id)
    status = CommandStatus(report.status)
    if command.status == status:
        return command  # a retried report
    if command.status in TERMINAL_STATUSES:
        raise CommandError(409, f"Command is already {command.status.value.lower()}")
    if command.status in NOT_ACCEPTED:
        # The ack was lost on the way; the result proves the camera accepted it.
        command.acknowledged_at = _now()
        command.delivered_at = command.delivered_at or command.acknowledged_at
    command.result = report.result
    _finish(db, command, status, report.error_code if status == CommandStatus.FAILED else None,
            "device", "command.completed")
    db.commit()
    db.refresh(command)
    return command


def cancel(db: Session, user: User, command: DeviceCommand) -> DeviceCommand:
    if command.status == CommandStatus.CANCELLED:
        return command
    if command.status not in NOT_ACCEPTED:
        raise CommandError(409, f"Command can't be cancelled: it is already {command.status.value.lower()}")
    _finish(db, command, CommandStatus.CANCELLED, None, "user", "command.cancelled")
    db.commit()
    db.refresh(command)
    return command

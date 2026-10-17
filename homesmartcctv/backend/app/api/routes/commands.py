"""Device command API: owners create, follow and cancel commands; the camera device
receives them (control WebSocket push, or polling) and acknowledges / reports back.
See services/commands.py for the lifecycle rules."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import WebSocketException
from sqlalchemy.orm import Session
from starlette import status as ws_status

from app.api.deps import get_current_user
from app.api.routes.devices import _get_owned_device
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.camera_device import CameraDevice
from app.models.device_command import DeviceCommand
from app.models.user import User
from app.schemas.command import AuditLogOut, CommandCreate, CommandOut, CommandResultReport
from app.services import commands as command_service
from app.services.commands import CommandError
from app.services.control import control_manager

router = APIRouter(tags=["commands"])
logger = logging.getLogger(__name__)


def _raise(exc: CommandError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _command_message(command: DeviceCommand) -> dict:
    return {"type": "command", "command": CommandOut.model_validate(command).model_dump(mode="json")}


@router.post("/devices/{device_id}/commands", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
async def create_command(
    device_id: uuid.UUID,
    payload: CommandCreate,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeviceCommand:
    device = _get_owned_device(db, device_id, current_user)
    try:
        command, created = command_service.create_command(db, current_user, device, payload)
    except CommandError as exc:
        _raise(exc)
    if not created:
        response.status_code = status.HTTP_200_OK  # an idempotent replay, not a new command
        return command
    if await control_manager.push(str(device.id), _command_message(command)):
        command_service.mark_delivered(db, [command])
        db.refresh(command)
    return command


@router.get("/devices/{device_id}/commands", response_model=list[CommandOut])
def list_commands(
    device_id: uuid.UUID,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DeviceCommand]:
    device = _get_owned_device(db, device_id, current_user)
    command_service.expire_overdue(db, device.id)
    return (
        db.query(DeviceCommand)
        .filter(DeviceCommand.camera_device_id == device.id)
        .order_by(DeviceCommand.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )


@router.get("/devices/{device_id}/commands/pending", response_model=list[CommandOut])
def pending_commands(
    device_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[DeviceCommand]:
    """Polled by the camera device: commands it hasn't accepted yet, now marked delivered."""
    device = _get_owned_device(db, device_id, current_user)
    return command_service.take_pending(db, device)


@router.get("/commands/{command_id}", response_model=CommandOut)
def get_command(
    command_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> DeviceCommand:
    try:
        return command_service.get_owned_command(db, current_user, command_id)
    except CommandError as exc:
        _raise(exc)


@router.post("/devices/{device_id}/commands/{command_id}/cancel", response_model=CommandOut)
def cancel_command(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeviceCommand:
    device = _get_owned_device(db, device_id, current_user)
    try:
        command = command_service.get_device_command(db, device, command_id)
        return command_service.cancel(db, current_user, command)
    except CommandError as exc:
        _raise(exc)


@router.post("/devices/{device_id}/commands/{command_id}/ack", response_model=CommandOut)
def acknowledge_command(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeviceCommand:
    device = _get_owned_device(db, device_id, current_user)
    try:
        return command_service.acknowledge(db, device, command_id)
    except CommandError as exc:
        _raise(exc)


@router.post("/devices/{device_id}/commands/{command_id}/result", response_model=CommandOut)
def report_command_result(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    report: CommandResultReport,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeviceCommand:
    device = _get_owned_device(db, device_id, current_user)
    try:
        return command_service.report_result(db, device, command_id, report)
    except CommandError as exc:
        _raise(exc)


@router.get("/devices/{device_id}/audit", response_model=list[AuditLogOut])
def list_audit(
    device_id: uuid.UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AuditLog]:
    device = _get_owned_device(db, device_id, current_user)
    return (
        db.query(AuditLog)
        .filter(AuditLog.camera_device_id == device.id)
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )


@router.websocket("/devices/{device_id}/control")
async def control(
    websocket: WebSocket,
    device_id: uuid.UUID,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> None:
    """The camera's command channel. Authenticated like signaling (the JWT travels as a
    query parameter, which the access log never records - see core/logging.py). On
    connect, everything still waiting is sent at once; afterwards new commands arrive
    as they are created. The camera only listens here and answers over REST."""
    try:
        user_id = uuid.UUID(decode_access_token(token)["sub"])
    except Exception as exc:
        raise WebSocketException(code=ws_status.WS_1008_POLICY_VIOLATION, reason="Invalid token") from exc
    device = db.get(CameraDevice, device_id)
    if device is None or device.user_id != user_id:
        raise WebSocketException(code=ws_status.WS_1008_POLICY_VIOLATION, reason="Device not found or not owned by you")

    await websocket.accept()
    key = str(device_id)
    await control_manager.connect(key, websocket)
    logger.info("Control connected: device_id=%s", device_id)
    try:
        for command in command_service.take_pending(db, device):
            await websocket.send_json(_command_message(command))
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        await control_manager.disconnect(key, websocket)
        logger.info("Control disconnected: device_id=%s", device_id)

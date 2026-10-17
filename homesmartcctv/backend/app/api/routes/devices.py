import json
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import WebSocketException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette import status as ws_status

from app.api.deps import get_current_user
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.camera_device import CameraDevice, CameraStatus
from app.models.camera_event import CameraEvent, EventType
from app.models.camera_video import CameraVideo
from app.models.user import User
from app.schemas.device import DeviceOut, DeviceRegisterRequest, HeartbeatRequest
from app.schemas.event import EventOut
from app.schemas.video import VideoOut
from app.services.captures import IMAGE_BACKED_EVENT_TYPES, capture_file_path, cleanup_expired_captures, save_capture
from app.services.device_status import apply_heartbeat, create_event, sync_online_status
from app.services.signaling import signaling_manager
from app.services.videos import cleanup_expired_videos, save_video, video_file_path

router = APIRouter(prefix="/devices", tags=["devices"])
logger = logging.getLogger(__name__)

MAX_CAPTURE_SIZE_BYTES = 8 * 1024 * 1024  # 8MB - generous for a single JPEG snapshot
MAX_VIDEO_SIZE_BYTES = 50 * 1024 * 1024  # 50MB - generous for a ~60s mobile-quality clip


def _with_image_path(event: CameraEvent) -> CameraEvent:
    event.image_path = (
        f"/devices/{event.camera_device_id}/captures/{event.id}/image"
        if event.event_type in IMAGE_BACKED_EVENT_TYPES
        else None
    )
    return event


def _with_video_path(video: CameraVideo) -> CameraVideo:
    video.video_path = f"/devices/{video.camera_device_id}/videos/{video.id}/file"
    return video


def _get_owned_device(db: Session, device_id: uuid.UUID, user: User) -> CameraDevice:
    device = db.get(CameraDevice, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    if device.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this device")
    return device


@router.post("/register", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def register_device(
    payload: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CameraDevice:
    existing = db.query(CameraDevice).filter(CameraDevice.device_uuid == payload.device_uuid).first()
    if existing:
        if existing.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Device already registered to another account"
            )
        device = existing
        device.device_name = payload.device_name
        device.platform = payload.platform
    else:
        device = CameraDevice(
            user_id=current_user.id,
            device_name=payload.device_name,
            device_uuid=payload.device_uuid,
            platform=payload.platform,
        )
        db.add(device)

    db.commit()
    db.refresh(device)
    device.is_online = sync_online_status(db, device)
    logger.info("Device registered: device_id=%s user_id=%s", device.id, current_user.id)
    return device


@router.get("", response_model=list[DeviceOut])
def list_devices(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[CameraDevice]:
    devices = db.query(CameraDevice).filter(CameraDevice.user_id == current_user.id).all()
    for device in devices:
        device.is_online = sync_online_status(db, device)
    return devices


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CameraDevice:
    device = _get_owned_device(db, device_id, current_user)
    device.is_online = sync_online_status(db, device)
    return device


@router.post("/{device_id}/heartbeat", response_model=DeviceOut)
def heartbeat(
    device_id: uuid.UUID,
    payload: HeartbeatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CameraDevice:
    device = _get_owned_device(db, device_id, current_user)
    device = apply_heartbeat(db, device, payload)
    device.is_online = True
    logger.info("Heartbeat received: device_id=%s", device.id)
    return device


@router.post("/{device_id}/camera/start", response_model=DeviceOut)
def start_camera(
    device_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CameraDevice:
    device = _get_owned_device(db, device_id, current_user)
    device.camera_status = CameraStatus.STREAMING
    db.add(device)
    db.commit()
    db.refresh(device)
    create_event(db, device.id, EventType.CAMERA_STARTED)
    device.is_online = sync_online_status(db, device)
    logger.info("Camera started: device_id=%s", device.id)
    return device


@router.post("/{device_id}/camera/stop", response_model=DeviceOut)
def stop_camera(
    device_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CameraDevice:
    device = _get_owned_device(db, device_id, current_user)
    device.camera_status = CameraStatus.IDLE
    db.add(device)
    db.commit()
    db.refresh(device)
    create_event(db, device.id, EventType.CAMERA_STOPPED)
    device.is_online = sync_online_status(db, device)
    logger.info("Camera stopped: device_id=%s", device.id)
    return device


@router.get("/{device_id}/events", response_model=list[EventOut])
def list_events(
    device_id: uuid.UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CameraEvent]:
    device = _get_owned_device(db, device_id, current_user)
    limit = max(1, min(limit, 200))
    events = (
        db.query(CameraEvent)
        .filter(CameraEvent.camera_device_id == device.id)
        .order_by(CameraEvent.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [_with_image_path(event) for event in events]


@router.post("/{device_id}/captures", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_capture(
    device_id: uuid.UUID,
    file: UploadFile,
    event_type: Literal["MANUAL_CAPTURE", "PERSON_DETECTED"] = Form(default="MANUAL_CAPTURE"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CameraEvent:
    device = _get_owned_device(db, device_id, current_user)

    if file.content_type not in ("image/jpeg", "image/jpg"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Only JPEG captures are accepted"
        )

    content = await file.read(MAX_CAPTURE_SIZE_BYTES + 1)
    if len(content) > MAX_CAPTURE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Capture too large")

    event = create_event(db, device.id, EventType(event_type))
    save_capture(device.id, event.id, content)
    logger.info("Capture saved: device_id=%s event_id=%s type=%s bytes=%d", device.id, event.id, event_type, len(content))

    cleanup_expired_captures(db)

    return _with_image_path(event)


@router.get("/{device_id}/captures/{event_id}/image")
def get_capture_image(
    device_id: uuid.UUID,
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    device = _get_owned_device(db, device_id, current_user)
    event = db.get(CameraEvent, event_id)
    if event is None or event.camera_device_id != device.id or event.event_type not in IMAGE_BACKED_EVENT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture not found")

    path = capture_file_path(device.id, event.id)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture has expired")

    return FileResponse(path, media_type="image/jpeg")


@router.post("/{device_id}/videos", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
async def create_video(
    device_id: uuid.UUID,
    file: UploadFile,
    started_at: datetime = Form(...),
    duration_seconds: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CameraVideo:
    device = _get_owned_device(db, device_id, current_user)

    if file.content_type not in ("video/mp4",):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Only MP4 segments are accepted")

    content = await file.read(MAX_VIDEO_SIZE_BYTES + 1)
    if len(content) > MAX_VIDEO_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Video segment too large")

    video = CameraVideo(camera_device_id=device.id, started_at=started_at, duration_seconds=duration_seconds)
    db.add(video)
    db.commit()
    db.refresh(video)
    save_video(device.id, video.id, content)
    logger.info(
        "Video segment saved: device_id=%s video_id=%s duration=%ds bytes=%d",
        device.id,
        video.id,
        duration_seconds,
        len(content),
    )

    cleanup_expired_videos(db)

    return _with_video_path(video)


@router.get("/{device_id}/videos", response_model=list[VideoOut])
def list_videos(
    device_id: uuid.UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CameraVideo]:
    device = _get_owned_device(db, device_id, current_user)
    limit = max(1, min(limit, 200))
    videos = (
        db.query(CameraVideo)
        .filter(CameraVideo.camera_device_id == device.id)
        .order_by(CameraVideo.started_at.desc())
        .limit(limit)
        .all()
    )
    return [_with_video_path(video) for video in videos]


@router.get("/{device_id}/videos/{video_id}/file")
def get_video_file(
    device_id: uuid.UUID,
    video_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    device = _get_owned_device(db, device_id, current_user)
    video = db.get(CameraVideo, video_id)
    if video is None or video.camera_device_id != device.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    path = video_file_path(device.id, video.id)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video has expired")

    return FileResponse(path, media_type="video/mp4")


@router.websocket("/{device_id}/signal")
async def signal(
    websocket: WebSocket,
    device_id: uuid.UUID,
    token: str = Query(...),
    role: Literal["camera", "viewer"] = Query(...),
    db: Session = Depends(get_db),
) -> None:
    """WebRTC signaling relay for one device. Not authenticated via the usual
    Authorization header - browsers/RN's WebSocket can't set custom headers on the
    handshake, so the JWT travels as a query param instead. That's why
    uvicorn's access logger is silenced (see core/logging.py): it would otherwise
    write this URL, token included, to disk on every connection.
    """
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except Exception as exc:
        raise WebSocketException(code=ws_status.WS_1008_POLICY_VIOLATION, reason="Invalid token") from exc

    device = db.get(CameraDevice, device_id)
    if device is None or device.user_id != user_id:
        raise WebSocketException(
            code=ws_status.WS_1008_POLICY_VIOLATION, reason="Device not found or not owned by you"
        )

    await websocket.accept()
    device_key = str(device_id)
    await signaling_manager.connect(device_key, role, websocket)
    logger.info("Signaling connected: device_id=%s role=%s user_id=%s", device_id, role, user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            await signaling_manager.relay(device_key, role, message)
    except WebSocketDisconnect:
        pass
    finally:
        await signaling_manager.disconnect(device_key, role, websocket)
        logger.info("Signaling disconnected: device_id=%s role=%s user_id=%s", device_id, role, user_id)

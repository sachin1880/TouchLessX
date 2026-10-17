import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.camera_device import CameraStatus, DevicePlatform, NetworkStatus


class DeviceRegisterRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=120)
    device_uuid: str = Field(min_length=1, max_length=255)
    platform: DevicePlatform


class HeartbeatRequest(BaseModel):
    battery_level: Optional[int] = Field(default=None, ge=0, le=100)
    network_status: Optional[NetworkStatus] = None
    camera_status: Optional[CameraStatus] = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    device_name: str
    device_uuid: str
    platform: DevicePlatform
    is_online: bool
    camera_status: CameraStatus
    last_seen: Optional[datetime]
    battery_level: Optional[int]
    network_status: Optional[NetworkStatus]
    created_at: datetime
    updated_at: datetime

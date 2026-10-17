import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_device_id: uuid.UUID
    started_at: datetime
    duration_seconds: int
    created_at: datetime
    # Relative API path to GET/stream the video file from.
    video_path: str

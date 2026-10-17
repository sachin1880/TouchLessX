import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.camera_event import EventType


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_device_id: uuid.UUID
    event_type: EventType
    event_metadata: Optional[dict[str, Any]] = None
    timestamp: datetime
    created_at: datetime
    # Populated only for MANUAL_CAPTURE events - the relative API path to GET the image from.
    image_path: Optional[str] = None

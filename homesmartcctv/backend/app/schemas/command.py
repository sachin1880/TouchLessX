import json
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.device_command import CommandStatus, CommandType

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
MAX_RESULT_BYTES = 4096


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SwitchCameraParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = whichever camera isn't in use now.
    facing: Optional[Literal["front", "back"]] = None


# The parameters each command type accepts. A type missing here can't be created.
PARAMETER_SCHEMAS: dict[CommandType, type[BaseModel]] = {
    CommandType.REFRESH_STATUS: NoParameters,
    CommandType.SWITCH_CAMERA: SwitchCameraParameters,
    CommandType.CAPTURE_SNAPSHOT: NoParameters,
}


class CommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_type: CommandType
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Chosen by the client and reused on a retry, so a repeated request returns the
    # original command instead of creating a duplicate.
    idempotency_key: str = Field(pattern=IDEMPOTENCY_KEY_PATTERN)
    ttl_seconds: Optional[int] = Field(default=None, ge=5)
    source: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")


class CommandResultReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["SUCCEEDED", "FAILED"]
    # A short machine-readable reason, e.g. "camera_not_streaming" - never free text
    # that could carry personal data or secrets into the audit log.
    error_code: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    result: Optional[dict[str, Any]] = None

    @field_validator("result")
    @classmethod
    def _small_result(cls, value: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if value is not None and len(json.dumps(value, default=str)) > MAX_RESULT_BYTES:
            raise ValueError(f"result must be at most {MAX_RESULT_BYTES} bytes as JSON")
        return value


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_device_id: uuid.UUID
    command_type: CommandType
    parameters: Optional[dict[str, Any]] = None
    status: CommandStatus
    idempotency_key: str
    source: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_type: str
    action: str
    camera_device_id: Optional[uuid.UUID] = None
    command_id: Optional[uuid.UUID] = None
    outcome: str
    details: Optional[dict[str, Any]] = None
    created_at: datetime

"""Audit logging: an append-only trail of security-relevant actions.

Entries are added to the caller's session and committed with the change they
describe, so an action and its audit record are saved together or not at all.
Only small, sanitized values belong in `details` - never tokens, passwords, media
or free text typed by a user.
"""

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def record(
    db: Session,
    *,
    action: str,
    outcome: str,
    actor_type: str = "user",
    user_id: Optional[uuid.UUID] = None,
    device_id: Optional[uuid.UUID] = None,
    command_id: Optional[uuid.UUID] = None,
    details: Optional[dict[str, Any]] = None,
) -> AuditLog:
    entry = AuditLog(
        actor_type=actor_type,
        action=action,
        outcome=outcome,
        user_id=user_id,
        camera_device_id=device_id,
        command_id=command_id,
        details=details,
    )
    db.add(entry)
    logger.info("Audit: action=%s outcome=%s device_id=%s command_id=%s", action, outcome, device_id, command_id)
    return entry

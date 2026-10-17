"""device_uuid unique per account, so one phone can be a camera on several accounts

Revision ID: f3c7a9e1b2d4
Revises: b25d74671c39
Create Date: 2026-10-17 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3c7a9e1b2d4'
down_revision: Union[str, None] = 'b25d74671c39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Relaxes a rule rather than adding one: no data changes. Each existing row already
    # satisfies the new (user_id, device_uuid) rule because device_uuid was unique.
    op.drop_index(op.f('ix_camera_devices_device_uuid'), table_name='camera_devices')
    op.create_index(op.f('ix_camera_devices_device_uuid'), 'camera_devices', ['device_uuid'], unique=False)
    op.create_unique_constraint(
        'uq_camera_devices_user_device_uuid', 'camera_devices', ['user_id', 'device_uuid']
    )


def downgrade() -> None:
    # Fails if a phone is now registered on more than one account - remove the extra
    # registrations first; there is no safe automatic choice of which to keep.
    op.drop_constraint('uq_camera_devices_user_device_uuid', 'camera_devices', type_='unique')
    op.drop_index(op.f('ix_camera_devices_device_uuid'), table_name='camera_devices')
    op.create_index(op.f('ix_camera_devices_device_uuid'), 'camera_devices', ['device_uuid'], unique=True)

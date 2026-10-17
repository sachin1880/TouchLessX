"""add person_detected event type

Revision ID: e1a2b3c4d5e6
Revises: d924f0506811
Create Date: 2026-09-19 19:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1a2b3c4d5e6'
down_revision = 'd924f0506811'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # See d924f0506811 - autogenerate doesn't detect new Python enum members.
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'PERSON_DETECTED'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE - not worth a full type rebuild here.
    pass

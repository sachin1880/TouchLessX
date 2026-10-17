"""add manual_capture event type

Revision ID: d924f0506811
Revises: d0232c00e819
Create Date: 2026-09-19 16:34:38.433153

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd924f0506811'
down_revision = 'd0232c00e819'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogenerate doesn't detect new Python enum members as a Postgres ENUM change -
    # written by hand. Postgres allows ALTER TYPE ... ADD VALUE inside a transaction as
    # long as the new value isn't used in that same transaction, which this migration
    # doesn't do.
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'MANUAL_CAPTURE'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE - removing an enum value requires
    # rebuilding the type, which isn't worth it for a rollback of an additive change.
    pass

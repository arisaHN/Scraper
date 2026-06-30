"""add ditano to site_enum

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE siteenum ADD VALUE IF NOT EXISTS 'ditano'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values; downgrade is a no-op.
    pass

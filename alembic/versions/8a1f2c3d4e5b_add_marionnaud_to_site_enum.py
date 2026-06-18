"""add marionnaud to site_enum

Revision ID: 8a1f2c3d4e5b
Revises: 47de47754486
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8a1f2c3d4e5b'
down_revision: Union[str, None] = '47de47754486'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE siteenum ADD VALUE IF NOT EXISTS 'marionnaud'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values; downgrade is a no-op.
    pass

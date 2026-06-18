"""add notino to site_enum

Revision ID: 47de47754486
Revises: 0006
Create Date: 2026-06-17 12:59:03.546993

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '47de47754486'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE siteenum ADD VALUE IF NOT EXISTS 'notino'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values; downgrade is a no-op.
    pass

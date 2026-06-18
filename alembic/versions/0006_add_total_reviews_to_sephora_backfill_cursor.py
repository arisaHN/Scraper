"""add total_reviews to sephora backfill cursor

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sephora_backfill_cursors",
        sa.Column("total_reviews", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sephora_backfill_cursors", "total_reviews")

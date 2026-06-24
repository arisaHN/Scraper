"""add category to products

Revision ID: 0007
Revises: 8a1f2c3d4e5b
Create Date: 2026-06-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "8a1f2c3d4e5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "category")

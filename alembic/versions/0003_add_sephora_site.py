"""add sephora to siteenum

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE siteenum ADD VALUE 'sephora'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values without recreating the type.
    # Leave the value in place — unused values are harmless.
    pass

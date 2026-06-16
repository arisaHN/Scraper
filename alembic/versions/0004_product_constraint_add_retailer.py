"""include retailer in product uniqueness constraint

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_product_site_external", "products", type_="unique")
    op.create_unique_constraint(
        "uq_product_site_external", "products", ["source_site", "external_id", "retailer"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_product_site_external", "products", type_="unique")
    op.create_unique_constraint(
        "uq_product_site_external", "products", ["source_site", "external_id"]
    )

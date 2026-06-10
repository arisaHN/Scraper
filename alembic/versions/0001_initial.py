"""initial

Revision ID: 0001
Revises:
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "source_site",
            sa.Enum("trustpilot", "amazon", "google", "bazaarvoice", name="siteenum"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text()),
        sa.Column("external_id", sa.String(512)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_site", "external_id", name="uq_product_site_external"),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column(
            "source_site",
            sa.Enum("trustpilot", "amazon", "google", "bazaarvoice", name="siteenum"),
            nullable=False,
        ),
        sa.Column("external_review_id", sa.String(512)),
        sa.Column("author", sa.Text()),
        sa.Column("rating", sa.Float()),
        sa.Column("title", sa.Text()),
        sa.Column("text", sa.Text()),
        sa.Column("review_date", sa.DateTime()),
        sa.Column("helpful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("scraped_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_site", "external_review_id", name="uq_review_site_external"
        ),
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column(
            "site",
            sa.Enum("trustpilot", "amazon", "google", "bazaarvoice", name="siteenum"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("running", "success", "failed", "partial", name="runstatus"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("reviews_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_msg", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("scrape_runs")
    op.drop_table("reviews")
    op.drop_table("products")
    op.drop_table("brands")
    op.execute("DROP TYPE IF EXISTS siteenum")
    op.execute("DROP TYPE IF EXISTS runstatus")

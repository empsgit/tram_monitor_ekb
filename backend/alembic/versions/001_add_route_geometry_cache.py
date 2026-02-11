"""Add route_geometry_cache and data_cache_meta tables.

Revision ID: 001
Revises:
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_geometry_cache",
        sa.Column("route_number", sa.String(10), primary_key=True),
        sa.Column("coords_json", JSONB, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "data_cache_meta",
        sa.Column("cache_key", sa.String(50), primary_key=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("data_cache_meta")
    op.drop_table("route_geometry_cache")

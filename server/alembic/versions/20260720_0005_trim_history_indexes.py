"""trim redundant price history indexes

Revision ID: 20260720_0005
Revises: 20260720_0004
Create Date: 2026-07-20 19:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0005"
down_revision: str | None = "20260720_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_price_observations_history_keyset",
        table_name="price_observations",
    )
    op.drop_index(
        "ix_price_observations_query_run_price",
        table_name="price_observations",
    )
    op.create_index(
        "ix_price_observations_query_run_price",
        "price_observations",
        ["search_query_id", "observed_at", "collection_run_id"],
        unique=False,
        postgresql_include=[
            "total_price_minor",
            "itinerary_id",
            "currency",
            "is_direct",
        ],
        postgresql_where=sa.text("is_lowest IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_price_observations_query_run_price",
        table_name="price_observations",
    )
    op.create_index(
        "ix_price_observations_query_run_price",
        "price_observations",
        ["search_query_id", "observed_at", "collection_run_id", "total_price_minor"],
        unique=False,
        postgresql_include=["itinerary_id", "currency", "is_direct"],
    )
    op.create_index(
        "ix_price_observations_history_keyset",
        "price_observations",
        ["search_query_id", "observed_at", "id"],
        unique=False,
    )

"""add calendar latest snapshots and fare read indexes

Revision ID: 20260720_0004
Revises: 20260720_0003
Create Date: 2026-07-20 18:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0004"
down_revision: str | None = "20260720_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "latest_calendar_price_snapshots",
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("collection_run_id", sa.Uuid(), nullable=True),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("departure_date", sa.Date(), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("lowest_price_minor", sa.Integer(), nullable=False),
        sa.Column("total_price_minor", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_endpoint", sa.String(length=160), nullable=False),
        sa.Column("direct_verified", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lowest_price_minor >= 0",
            name=op.f("ck_latest_calendar_price_snapshots_calendar_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_price_minor IS NULL OR total_price_minor >= 0",
            name=op.f("ck_latest_calendar_price_snapshots_calendar_total_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f("fk_latest_calendar_price_snapshots_collection_run_id_collection_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_latest_calendar_price_snapshots_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name=op.f("fk_latest_calendar_price_snapshots_search_query_id_search_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_latest_calendar_price_snapshots")),
        sa.UniqueConstraint(
            "search_query_id",
            "departure_date",
            "return_date",
            "currency",
            name=op.f("uq_latest_calendar_price_snapshots_date_pair"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_latest_calendar_price_snapshots_search_dates",
        "latest_calendar_price_snapshots",
        ["search_query_id", "departure_date", "return_date", "observed_at"],
        unique=False,
        postgresql_include=["currency", "lowest_price_minor", "total_price_minor"],
    )
    op.create_index(
        "ix_price_observations_query_run_price",
        "price_observations",
        ["search_query_id", "observed_at", "collection_run_id", "total_price_minor"],
        unique=False,
        postgresql_include=["itinerary_id", "currency", "is_direct"],
    )
    op.create_index(
        "ix_collection_runs_query_success_finished",
        "collection_runs",
        ["search_query_id", "finished_at", "id"],
        unique=False,
        postgresql_include=["offer_count", "error_code"],
        postgresql_where=sa.text("status = 'succeeded' AND finished_at IS NOT NULL"),
    )
    op.create_index(
        "ix_fare_offers_run_price",
        "fare_offers",
        ["collection_run_id", "total_price_minor", "id"],
        unique=False,
        postgresql_include=["itinerary_id", "currency", "cabin"],
    )


def downgrade() -> None:
    op.drop_index("ix_fare_offers_run_price", table_name="fare_offers")
    op.drop_index("ix_collection_runs_query_success_finished", table_name="collection_runs")
    op.drop_index("ix_price_observations_query_run_price", table_name="price_observations")
    op.drop_index(
        "ix_latest_calendar_price_snapshots_search_dates",
        table_name="latest_calendar_price_snapshots",
    )
    op.drop_table("latest_calendar_price_snapshots")

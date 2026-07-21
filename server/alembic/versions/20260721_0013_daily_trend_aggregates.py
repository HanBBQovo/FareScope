"""add maintained daily dashboard trend aggregates

Revision ID: 20260721_0013
Revises: 20260720_0012
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0013"
down_revision: str | None = "20260720_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_trend_aggregates",
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("direct_only", sa.Boolean(), nullable=False),
        sa.Column("lowest_price_minor", sa.Integer(), nullable=False),
        sa.Column("highest_price_minor", sa.Integer(), nullable=False),
        sa.Column("price_sum_minor", sa.BigInteger(), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "highest_price_minor >= lowest_price_minor",
            name=op.f("ck_daily_trend_aggregates_valid_price_range"),
        ),
        sa.CheckConstraint(
            "first_observed_at <= last_observed_at",
            name=op.f("ck_daily_trend_aggregates_valid_observation_range"),
        ),
        sa.CheckConstraint(
            "lowest_price_minor >= 0",
            name=op.f("ck_daily_trend_aggregates_lowest_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "price_sum_minor >= 0",
            name=op.f("ck_daily_trend_aggregates_price_sum_nonnegative"),
        ),
        sa.CheckConstraint(
            "sample_count > 0",
            name=op.f("ck_daily_trend_aggregates_sample_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name=op.f("fk_daily_trend_aggregates_search_query_id_search_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "search_query_id",
            "observation_date",
            "currency",
            "direct_only",
            name=op.f("pk_daily_trend_aggregates"),
        ),
    )
    op.create_index(
        "ix_daily_trend_aggregates_lookup",
        "daily_trend_aggregates",
        ["search_query_id", "currency", "direct_only", "observation_date"],
        unique=False,
        postgresql_include=[
            "lowest_price_minor",
            "highest_price_minor",
            "price_sum_minor",
            "sample_count",
        ],
    )
    op.create_table(
        "daily_trend_aggregate_coverage",
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("source_last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name=op.f("fk_daily_trend_aggregate_coverage_search_query_id_search_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "search_query_id",
            "observation_date",
            name=op.f("pk_daily_trend_aggregate_coverage"),
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_trend_aggregate_coverage")
    op.drop_index(
        "ix_daily_trend_aggregates_lookup",
        table_name="daily_trend_aggregates",
    )
    op.drop_table("daily_trend_aggregates")

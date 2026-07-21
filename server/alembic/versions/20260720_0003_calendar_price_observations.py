"""add partitioned calendar price observations

Revision ID: 20260720_0003
Revises: 20260720_0002
Create Date: 2026-07-20 16:10:00
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260720_0003"
down_revision: str | None = "20260720_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _create_calendar_partitions() -> None:
    anchor = _month_start(datetime.now(UTC).date())
    for offset in range(-1, 3):
        start = _shift_month(anchor, offset)
        end = _shift_month(start, 1)
        name = f"calendar_price_observations_y{start.year:04d}m{start.month:02d}"
        start_iso = datetime(start.year, start.month, 1, tzinfo=UTC).isoformat()
        end_iso = datetime(end.year, end.month, 1, tzinfo=UTC).isoformat()
        op.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {name} "
                "PARTITION OF calendar_price_observations "
                f"FOR VALUES FROM (TIMESTAMPTZ '{start_iso}') "
                f"TO (TIMESTAMPTZ '{end_iso}')"
            )
        )


def upgrade() -> None:
    op.create_table(
        "calendar_price_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("collection_run_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("departure_date", sa.Date(), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=True),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("lowest_price_minor", sa.Integer(), nullable=False),
        sa.Column("total_price_minor", sa.Integer(), nullable=True),
        sa.Column("source_endpoint", sa.String(length=160), nullable=False),
        sa.Column(
            "observation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lowest_price_minor >= 0",
            name=op.f(
                "ck_calendar_price_observations_calendar_price_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "total_price_minor IS NULL OR total_price_minor >= 0",
            name=op.f(
                "ck_calendar_price_observations_calendar_total_price_nonnegative"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f(
                "fk_calendar_price_observations_collection_run_id_collection_runs"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_calendar_price_observations_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name=op.f(
                "fk_calendar_price_observations_search_query_id_search_queries"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            "observed_at",
            name=op.f("pk_calendar_price_observations"),
        ),
        sa.UniqueConstraint(
            "observed_at",
            "collection_run_id",
            "fingerprint",
            name=op.f(
                "uq_calendar_price_observations_observed_at_collection_run_id_fingerprint"
            ),
        ),
        postgresql_partition_by="RANGE (observed_at)",
    )
    op.create_index(
        "ix_calendar_price_observations_search_dates",
        "calendar_price_observations",
        ["search_query_id", "departure_date", "return_date", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_price_observations_history_keyset",
        "calendar_price_observations",
        ["search_query_id", "observed_at", "id"],
        unique=False,
    )
    _create_calendar_partitions()


def downgrade() -> None:
    op.drop_index(
        "ix_calendar_price_observations_history_keyset",
        table_name="calendar_price_observations",
    )
    op.drop_index(
        "ix_calendar_price_observations_search_dates",
        table_name="calendar_price_observations",
    )
    op.drop_table("calendar_price_observations")

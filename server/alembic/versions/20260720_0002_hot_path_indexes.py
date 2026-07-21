"""add hot path indexes and latest price snapshots

Revision ID: 20260720_0002
Revises: 20260720_0001
Create Date: 2026-07-20 13:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0002"
down_revision: str | None = "20260720_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_subscriptions_user_created",
        "subscriptions",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_due_keyset",
        "subscriptions",
        ["next_due_at", "id"],
        unique=False,
        postgresql_where=sa.text("enabled IS TRUE AND next_due_at IS NOT NULL"),
    )
    op.create_index(
        "ix_search_legs_first_route_date",
        "search_legs",
        ["origin_code", "destination_code", "departure_date", "search_query_id"],
        unique=False,
        postgresql_where=sa.text("position = 0"),
    )
    op.create_index(
        "ix_collection_runs_pending_keyset",
        "collection_runs",
        ["scheduled_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_collection_runs_recovery_keyset",
        "collection_runs",
        ["lease_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('leased', 'running')"),
    )
    op.create_index(
        "ix_price_observations_history_keyset",
        "price_observations",
        ["search_query_id", "observed_at", "id"],
        unique=False,
    )

    op.create_table(
        "latest_price_snapshots",
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("collection_run_id", sa.Uuid(), nullable=False),
        sa.Column("itinerary_id", sa.Uuid(), nullable=True),
        sa.Column("fare_offer_id", sa.Uuid(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_price_minor", sa.Integer(), nullable=False),
        sa.Column("is_direct", sa.Boolean(), nullable=False),
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
            "total_price_minor >= 0",
            name=op.f("ck_latest_price_snapshots_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f("fk_latest_price_snapshots_collection_run_id_collection_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fare_offer_id"],
            ["fare_offers.id"],
            name=op.f("fk_latest_price_snapshots_fare_offer_id_fare_offers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["itinerary_id"],
            ["itineraries.id"],
            name=op.f("fk_latest_price_snapshots_itinerary_id_itineraries"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_latest_price_snapshots_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name=op.f("fk_latest_price_snapshots_search_query_id_search_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_latest_price_snapshots")),
        sa.UniqueConstraint(
            "search_query_id",
            "currency",
            "is_direct",
            name=op.f(
                "uq_latest_price_snapshots_search_query_id_currency_is_direct"
            ),
        ),
    )
    op.create_index(
        "ix_latest_price_snapshots_price",
        "latest_price_snapshots",
        ["currency", "is_direct", "total_price_minor", "search_query_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_latest_price_snapshots_price",
        table_name="latest_price_snapshots",
    )
    op.drop_table("latest_price_snapshots")
    op.drop_index(
        "ix_price_observations_history_keyset",
        table_name="price_observations",
    )
    op.drop_index("ix_collection_runs_recovery_keyset", table_name="collection_runs")
    op.drop_index("ix_collection_runs_pending_keyset", table_name="collection_runs")
    op.drop_index("ix_search_legs_first_route_date", table_name="search_legs")
    op.drop_index("ix_subscriptions_due_keyset", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_created", table_name="subscriptions")

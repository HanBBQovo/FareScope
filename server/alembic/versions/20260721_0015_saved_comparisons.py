"""add owner-scoped saved route comparisons

Revision ID: 20260721_0015
Revises: 20260721_0014
Create Date: 2026-07-21 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0015"
down_revision: str | None = "20260721_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_subscriptions_user_id_id",
        "subscriptions",
        ["user_id", "id"],
    )
    op.create_table(
        "comparison_views",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("trend_days", sa.SmallInteger(), server_default="30", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("configured_route_count", sa.SmallInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "configured_route_count BETWEEN 2 AND 8",
            name="ck_comparison_views_valid_route_count",
        ),
        sa.CheckConstraint(
            "trend_days IN (7, 30, 90)",
            name="ck_comparison_views_valid_trend_days",
        ),
        sa.CheckConstraint("version > 0", name="ck_comparison_views_version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "id",
            name="uq_comparison_views_user_id_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_comparison_views_user_normalized_name",
        ),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_comparison_views_user_idempotency",
        ),
    )
    op.create_index(
        "ix_comparison_views_user_created",
        "comparison_views",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "comparison_view_items",
        sa.Column("comparison_view_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 7",
            name="ck_comparison_view_items_valid_position",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "comparison_view_id"],
            ["comparison_views.user_id", "comparison_views.id"],
            name="fk_comparison_view_items_owner_view",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "subscription_id"],
            ["subscriptions.user_id", "subscriptions.id"],
            name="fk_comparison_view_items_owner_subscription",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("comparison_view_id", "subscription_id"),
        sa.UniqueConstraint(
            "comparison_view_id",
            "position",
            name="uq_comparison_view_items_view_position",
        ),
    )
    op.create_index(
        "ix_comparison_view_items_subscription_view",
        "comparison_view_items",
        ["subscription_id", "comparison_view_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_comparison_view_items_subscription_view",
        table_name="comparison_view_items",
    )
    op.drop_table("comparison_view_items")
    op.drop_index("ix_comparison_views_user_created", table_name="comparison_views")
    op.drop_table("comparison_views")
    op.drop_constraint(
        "uq_subscriptions_user_id_id",
        "subscriptions",
        type_="unique",
    )

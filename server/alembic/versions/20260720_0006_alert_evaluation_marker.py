"""add durable marker for collection alert evaluation

Revision ID: 20260720_0006
Revises: 20260720_0005
Create Date: 2026-07-20 20:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0006"
down_revision: str | None = "20260720_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_runs",
        sa.Column("alerts_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_collection_runs_alerts_pending",
        "collection_runs",
        ["status", "alerts_evaluated_at", "finished_at", "id"],
        postgresql_where=sa.text("status = 'succeeded' AND alerts_evaluated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_collection_runs_alerts_pending", table_name="collection_runs")
    op.drop_column("collection_runs", "alerts_evaluated_at")

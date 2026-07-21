"""add bounded collection health index

Revision ID: 20260720_0007
Revises: 20260720_0006
Create Date: 2026-07-20 22:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0007"
down_revision: str | None = "20260720_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_collection_runs_query_terminal_finished",
        "collection_runs",
        ["search_query_id", "finished_at", "id"],
        unique=False,
        postgresql_include=["status"],
        postgresql_where=sa.text(
            "finished_at IS NOT NULL "
            "AND status IN ('succeeded', 'failed', 'canceled')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collection_runs_query_terminal_finished",
        table_name="collection_runs",
    )

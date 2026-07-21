"""add observation partition archive schema

Revision ID: 20260720_0012
Revises: 20260720_0011
Create Date: 2026-07-21 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0012"
down_revision: str | None = "20260720_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS farescope_archive")


def downgrade() -> None:
    # RESTRICT deliberately refuses to erase archived observations.
    op.execute("DROP SCHEMA IF EXISTS farescope_archive RESTRICT")

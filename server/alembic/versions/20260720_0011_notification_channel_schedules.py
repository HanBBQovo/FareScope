"""add per-channel notification delivery schedules

Revision ID: 20260720_0011
Revises: 20260720_0010
Create Date: 2026-07-21 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260720_0011"
down_revision: str | None = "20260720_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_channels",
        sa.Column("timezone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_channels",
        sa.Column("quiet_hours_start", sa.Time(), nullable=True),
    )
    op.add_column(
        "notification_channels",
        sa.Column("quiet_hours_end", sa.Time(), nullable=True),
    )
    op.add_column(
        "notification_channels",
        sa.Column(
            "allowed_weekdays",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "quiet_hours_pair",
        "notification_channels",
        "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)",
    )
    op.create_check_constraint(
        "schedule_timezone_required",
        "notification_channels",
        "(quiet_hours_start IS NULL AND allowed_weekdays IS NULL) OR timezone IS NOT NULL",
    )
    op.create_check_constraint(
        "allowed_weekdays_array",
        "notification_channels",
        "allowed_weekdays IS NULL OR jsonb_typeof(allowed_weekdays) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_notification_channels_allowed_weekdays_array",
        "notification_channels",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_channels_schedule_timezone_required",
        "notification_channels",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_channels_quiet_hours_pair",
        "notification_channels",
        type_="check",
    )
    op.drop_column("notification_channels", "allowed_weekdays")
    op.drop_column("notification_channels", "quiet_hours_end")
    op.drop_column("notification_channels", "quiet_hours_start")
    op.drop_column("notification_channels", "timezone")

"""separate notification target price from fare result filtering

Revision ID: 20260720_0010
Revises: 20260720_0009
Create Date: 2026-07-20 16:45:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0010"
down_revision: str | None = "20260720_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older clients used the same value for result filtering and the generated
    # notification rule. Clear only rows that can be proven to have that legacy
    # coupling so current prices above the target remain visible.
    op.execute(
        """
        UPDATE subscription_filters AS filters
        SET max_price_minor = NULL,
            currency = NULL,
            updated_at = now()
        FROM alert_rules AS rules
        WHERE rules.subscription_id = filters.subscription_id
          AND rules.rule_type = 'price_threshold'
          AND rules.rule_config ->> 'source' = 'subscription_target_price'
          AND rules.threshold_price_minor = filters.max_price_minor
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE subscription_filters AS filters
        SET max_price_minor = rules.threshold_price_minor,
            currency = rules.threshold_currency,
            updated_at = now()
        FROM alert_rules AS rules
        WHERE rules.subscription_id = filters.subscription_id
          AND rules.rule_type = 'price_threshold'
          AND rules.rule_config ->> 'source' = 'subscription_target_price'
          AND filters.max_price_minor IS NULL
        """
    )

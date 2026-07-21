from datetime import datetime, time
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    AlertRuleType,
    DeliveryStatus,
    NotificationChannelType,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("user_id", "name"),
        CheckConstraint(
            "channel_type IN ('email', 'telegram', 'bark', 'pushplus', 'webhook')",
            name="valid_channel_type",
        ),
        CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)",
            name="quiet_hours_pair",
        ),
        CheckConstraint(
            "(quiet_hours_start IS NULL AND allowed_weekdays IS NULL) OR timezone IS NOT NULL",
            name="schedule_timezone_required",
        ),
        CheckConstraint(
            "allowed_weekdays IS NULL OR jsonb_typeof(allowed_weekdays) = 'array'",
            name="allowed_weekdays_array",
        ),
        Index("ix_notification_channels_user_enabled", "user_id", "enabled"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[str] = mapped_column(
        String(24), default=NotificationChannelType.EMAIL.value, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    config_redacted: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    quiet_hours_start: Mapped[time | None] = mapped_column(Time(timezone=False))
    quiet_hours_end: Mapped[time | None] = mapped_column(Time(timezone=False))
    allowed_weekdays: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AlertRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('price_threshold', 'absolute_drop', 'percentage_drop', "
            "'new_low', 'direct_available', 'matching_itinerary', 'round_trip_range', "
            "'data_stale', 'schema_drift')",
            name="valid_rule_type",
        ),
        CheckConstraint("cooldown_seconds >= 0", name="cooldown_nonnegative"),
        CheckConstraint(
            "threshold_price_minor IS NULL OR threshold_price_minor >= 0",
            name="price_nonnegative",
        ),
        CheckConstraint(
            "threshold_percentage IS NULL OR "
            "(threshold_percentage > 0 AND threshold_percentage <= 10000)",
            name="percentage_basis_points",
        ),
        Index("ix_alert_rules_subscription_enabled", "subscription_id", "enabled"),
        Index("ix_alert_rules_user_enabled", "user_id", "enabled"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_type: Mapped[str] = mapped_column(
        String(40), default=AlertRuleType.PRICE_THRESHOLD.value, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    threshold_price_minor: Mapped[int | None] = mapped_column(Integer)
    threshold_currency: Mapped[str | None] = mapped_column(String(3))
    threshold_percentage: Mapped[int | None] = mapped_column(SmallInteger)
    comparison_window_days: Mapped[int | None] = mapped_column(SmallInteger)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=21_600, nullable=False)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time(timezone=False))
    quiet_hours_end: Mapped[time | None] = mapped_column(Time(timezone=False))
    quiet_hours_timezone: Mapped[str | None] = mapped_column(String(64))
    rule_config: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class AlertRuleChannel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alert_rule_channels"
    __table_args__ = (UniqueConstraint("alert_rule_id", "notification_channel_id"),)

    alert_rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    notification_channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )


class AlertEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key"),
        Index("ix_alert_events_user_created", "user_id", "created_at"),
        Index("ix_alert_events_rule_created", "alert_rule_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    alert_rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL")
    )
    deduplication_key: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    event_payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    suppressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppression_reason: Mapped[str | None] = mapped_column(String(160))


class NotificationDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("alert_event_id", "notification_channel_id"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'succeeded', 'failed', 'suppressed')",
            name="valid_status",
        ),
        CheckConstraint("attempt_count >= 0", name="attempts_nonnegative"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    alert_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False
    )
    notification_channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=DeliveryStatus.PENDING.value, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(180))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    response_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

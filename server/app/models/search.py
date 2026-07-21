from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TripType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SearchQuery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint("query_hash"),
        CheckConstraint("trip_type IN ('one_way', 'round_trip')", name="valid_trip_type"),
        CheckConstraint("adults >= 1", name="adults_positive"),
        CheckConstraint("children >= 0", name="children_nonnegative"),
        CheckConstraint("infants >= 0", name="infants_nonnegative"),
        Index("ix_search_queries_provider_updated", "provider", "updated_at"),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trip_type: Mapped[str] = mapped_column(
        String(16), default=TripType.ONE_WAY.value, nullable=False
    )
    adults: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    children: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    infants: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    cabin: Mapped[str] = mapped_column(String(32), default="economy", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    direct_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    normalized_query: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class SearchLeg(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "search_legs"
    __table_args__ = (
        UniqueConstraint("search_query_id", "position"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("origin_code <> destination_code", name="different_airports"),
        Index("ix_search_legs_route_date", "origin_code", "destination_code", "departure_date"),
        Index(
            "ix_search_legs_first_route_date",
            "origin_code",
            "destination_code",
            "departure_date",
            "search_query_id",
            postgresql_where=text("position = 0"),
        ),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    origin_code: Mapped[str] = mapped_column(String(8), nullable=False)
    destination_code: Mapped[str] = mapped_column(String(8), nullable=False)
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("poll_interval_seconds >= 300", name="poll_interval_minimum"),
        Index("ix_subscriptions_user_enabled", "user_id", "enabled"),
        Index("ix_subscriptions_due", "enabled", "next_due_at"),
        Index("ix_subscriptions_user_created", "user_id", "created_at", "id"),
        Index(
            "ix_subscriptions_due_keyset",
            "next_due_at",
            "id",
            postgresql_where=text("enabled IS TRUE AND next_due_at IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=21_600, nullable=False)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class SubscriptionFilter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscription_filters"
    __table_args__ = (
        UniqueConstraint("subscription_id"),
        CheckConstraint(
            "max_price_minor IS NULL OR max_price_minor >= 0", name="price_nonnegative"
        ),
        CheckConstraint("max_stops IS NULL OR max_stops >= 0", name="stops_nonnegative"),
        CheckConstraint(
            "max_duration_minutes IS NULL OR max_duration_minutes > 0",
            name="duration_positive",
        ),
    )

    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    airline_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    origin_airport_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    destination_airport_codes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    max_price_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    max_stops: Mapped[int | None] = mapped_column(SmallInteger)
    max_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    departure_time_start_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    departure_time_end_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    additional_filters: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

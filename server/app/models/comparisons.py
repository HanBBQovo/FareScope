from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ComparisonView(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "comparison_views"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "id",
            name="uq_comparison_views_user_id_id",
        ),
        UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_comparison_views_user_normalized_name",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_comparison_views_user_idempotency",
        ),
        CheckConstraint("trend_days IN (7, 30, 90)", name="valid_trend_days"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "configured_route_count BETWEEN 2 AND 8",
            name="valid_route_count",
        ),
        Index("ix_comparison_views_user_created", "user_id", "created_at", "id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    trend_days: Mapped[int] = mapped_column(SmallInteger, default=30, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    configured_route_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class ComparisonViewItem(Base):
    __tablename__ = "comparison_view_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ("user_id", "comparison_view_id"),
            ("comparison_views.user_id", "comparison_views.id"),
            name="fk_comparison_view_items_owner_view",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("user_id", "subscription_id"),
            ("subscriptions.user_id", "subscriptions.id"),
            name="fk_comparison_view_items_owner_subscription",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "comparison_view_id",
            "position",
            name="uq_comparison_view_items_view_position",
        ),
        CheckConstraint("position BETWEEN 0 AND 7", name="valid_position"),
        Index(
            "ix_comparison_view_items_subscription_view",
            "subscription_id",
            "comparison_view_id",
        ),
    )

    comparison_view_id: Mapped[UUID] = mapped_column(primary_key=True)
    subscription_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

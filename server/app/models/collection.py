from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CollectionStatus
from app.models.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Provider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("code"),)

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    adapter_version: Mapped[str | None] = mapped_column(String(80))


class CollectionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            "status IN ('pending', 'leased', 'running', 'succeeded', 'failed', 'canceled')",
            name="valid_status",
        ),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="valid_attempts"),
        Index("ix_collection_runs_query_scheduled", "search_query_id", "scheduled_at"),
        Index("ix_collection_runs_status_lease", "status", "lease_expires_at"),
        Index(
            "ix_collection_runs_pending_keyset",
            "scheduled_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_collection_runs_recovery_keyset",
            "lease_expires_at",
            "id",
            postgresql_where=text("status IN ('leased', 'running')"),
        ),
        Index(
            "ix_collection_runs_query_success_finished",
            "search_query_id",
            "finished_at",
            "id",
            postgresql_include=("offer_count", "error_code"),
            postgresql_where=text("status = 'succeeded' AND finished_at IS NOT NULL"),
        ),
        Index(
            "ix_collection_runs_query_terminal_finished",
            "search_query_id",
            "finished_at",
            "id",
            postgresql_include=("status",),
            postgresql_where=text(
                "finished_at IS NOT NULL AND status IN ('succeeded', 'failed', 'canceled')"
            ),
        ),
        Index(
            "ix_collection_runs_alerts_pending",
            "status",
            "alerts_evaluated_at",
            "finished_at",
            "id",
            postgresql_where=text("status = 'succeeded' AND alerts_evaluated_at IS NULL"),
        ),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=CollectionStatus.PENDING.value, nullable=False
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alerts_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upstream_status: Mapped[str | None] = mapped_column(String(80))
    schema_fingerprint: Mapped[str | None] = mapped_column(String(128))
    itinerary_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    offer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    run_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class CollectionArtifact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "collection_artifacts"
    __table_args__ = (
        UniqueConstraint("collection_run_id", "artifact_type", "checksum_sha256"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
    )

    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(60), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_encoding: Mapped[str | None] = mapped_column(String(40))
    redaction_version: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchemaObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "schema_observations"
    __table_args__ = (
        UniqueConstraint("provider_id", "endpoint", "schema_fingerprint"),
        Index("ix_schema_observations_provider_created", "provider_id", "created_at"),
    )

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL")
    )
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    field_summary: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Itinerary(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "itineraries"
    __table_args__ = (
        UniqueConstraint("collection_run_id", "fingerprint"),
        CheckConstraint("total_duration_minutes > 0", name="duration_positive"),
        CheckConstraint("stop_count >= 0", name="stops_nonnegative"),
        Index("ix_itineraries_query_created", "search_query_id", "created_at"),
    )

    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    provider_itinerary_id: Mapped[str | None] = mapped_column(String(240))
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    total_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_direct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    leg_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    itinerary_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )


class Segment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("itinerary_id", "position"),
        CheckConstraint("position >= 0 AND leg_position >= 0", name="positions_nonnegative"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
    )

    itinerary_id: Mapped[UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    leg_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    marketing_airline_code: Mapped[str] = mapped_column(String(8), nullable=False)
    operating_airline_code: Mapped[str | None] = mapped_column(String(8))
    flight_number: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_airport_code: Mapped[str] = mapped_column(String(8), nullable=False)
    destination_airport_code: Mapped[str] = mapped_column(String(8), nullable=False)
    departure_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    departure_local: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    arrival_local: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    departure_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    arrival_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    aircraft_code: Mapped[str | None] = mapped_column(String(24))
    segment_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class FareOffer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "fare_offers"
    __table_args__ = (
        UniqueConstraint("collection_run_id", "itinerary_id", "fingerprint"),
        CheckConstraint("total_price_minor >= 0", name="total_price_nonnegative"),
        CheckConstraint(
            "base_price_minor IS NULL OR base_price_minor >= 0", name="base_price_nonnegative"
        ),
        CheckConstraint("tax_minor IS NULL OR tax_minor >= 0", name="tax_nonnegative"),
        Index("ix_fare_offers_itinerary_price", "itinerary_id", "total_price_minor"),
        Index(
            "ix_fare_offers_run_price",
            "collection_run_id",
            "total_price_minor",
            "id",
            postgresql_include=("itinerary_id", "currency", "cabin"),
        ),
    )

    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    itinerary_id: Mapped[UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE"), nullable=False
    )
    provider_offer_id: Mapped[str | None] = mapped_column(String(240))
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    cabin: Mapped[str] = mapped_column(String(32), nullable=False)
    fare_family: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price_minor: Mapped[int | None] = mapped_column(Integer)
    tax_minor: Mapped[int | None] = mapped_column(Integer)
    seats_remaining: Mapped[int | None] = mapped_column(SmallInteger)
    baggage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    refund_change_rules: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint("observed_at", "collection_run_id", "offer_fingerprint"),
        CheckConstraint("total_price_minor >= 0", name="price_nonnegative"),
        Index("ix_price_observations_search_observed", "search_query_id", "observed_at"),
        Index(
            "ix_price_observations_query_run_price",
            "search_query_id",
            "observed_at",
            "collection_run_id",
            postgresql_include=(
                "total_price_minor",
                "itinerary_id",
                "currency",
                "is_direct",
            ),
            postgresql_where=text("is_lowest IS TRUE"),
        ),
        {"postgresql_partition_by": "RANGE (observed_at)"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    itinerary_id: Mapped[UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE"), nullable=False
    )
    fare_offer_id: Mapped[UUID] = mapped_column(
        ForeignKey("fare_offers.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    offer_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    is_lowest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_direct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )


class CalendarPriceObservation(Base):
    """Date-level prices returned by a provider calendar endpoint.

    Calendar prices are intentionally independent from itineraries: a calendar response
    generally has no flight identity, stop count, or directness guarantee.  Keeping these
    observations separate prevents us from inventing itinerary rows while retaining the
    date matrix needed for trend and date-selection views.
    """

    __tablename__ = "calendar_price_observations"
    __table_args__ = (
        UniqueConstraint("observed_at", "collection_run_id", "fingerprint"),
        CheckConstraint("lowest_price_minor >= 0", name="calendar_price_nonnegative"),
        CheckConstraint(
            "total_price_minor IS NULL OR total_price_minor >= 0",
            name="calendar_total_price_nonnegative",
        ),
        Index(
            "ix_calendar_price_observations_search_dates",
            "search_query_id",
            "departure_date",
            "return_date",
            "observed_at",
        ),
        Index(
            "ix_calendar_price_observations_history_keyset",
            "search_query_id",
            "observed_at",
            "id",
        ),
        {"postgresql_partition_by": "RANGE (observed_at)"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    return_date: Mapped[date | None] = mapped_column(Date)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lowest_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_minor: Mapped[int | None] = mapped_column(Integer)
    source_endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    observation_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )


class LatestCalendarPriceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Latest provider calendar value for one outbound/return date pair."""

    __tablename__ = "latest_calendar_price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "search_query_id",
            "departure_date",
            "return_date",
            "currency",
            name="uq_latest_calendar_price_snapshots_date_pair",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("lowest_price_minor >= 0", name="calendar_price_nonnegative"),
        CheckConstraint(
            "total_price_minor IS NULL OR total_price_minor >= 0",
            name="calendar_total_price_nonnegative",
        ),
        Index(
            "ix_latest_calendar_price_snapshots_search_dates",
            "search_query_id",
            "departure_date",
            "return_date",
            "observed_at",
            postgresql_include=("currency", "lowest_price_minor", "total_price_minor"),
        ),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL")
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    return_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lowest_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_minor: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    direct_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LatestPriceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "latest_price_snapshots"
    __table_args__ = (
        UniqueConstraint("search_query_id", "currency", "is_direct"),
        CheckConstraint("total_price_minor >= 0", name="price_nonnegative"),
        Index(
            "ix_latest_price_snapshots_price",
            "currency",
            "is_direct",
            "total_price_minor",
            "search_query_id",
        ),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE"), nullable=False
    )
    itinerary_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("itineraries.id", ondelete="SET NULL")
    )
    fare_offer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fare_offers.id", ondelete="SET NULL")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    is_direct: Mapped[bool] = mapped_column(Boolean, nullable=False)


class DailyPriceAggregate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "daily_price_aggregates"
    __table_args__ = (
        UniqueConstraint("search_query_id", "service_date", "currency", "is_direct"),
        CheckConstraint("sample_count > 0", name="sample_count_positive"),
        CheckConstraint("lowest_price_minor >= 0", name="price_nonnegative"),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_direct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lowest_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyTrendAggregate(TimestampMixin, Base):
    """Exact daily summary of per-run minima for Dashboard-compatible filters."""

    __tablename__ = "daily_trend_aggregates"
    __table_args__ = (
        CheckConstraint("lowest_price_minor >= 0", name="lowest_price_nonnegative"),
        CheckConstraint(
            "highest_price_minor >= lowest_price_minor",
            name="valid_price_range",
        ),
        CheckConstraint("price_sum_minor >= 0", name="price_sum_nonnegative"),
        CheckConstraint("sample_count > 0", name="sample_count_positive"),
        CheckConstraint(
            "first_observed_at <= last_observed_at",
            name="valid_observation_range",
        ),
        Index(
            "ix_daily_trend_aggregates_lookup",
            "search_query_id",
            "currency",
            "direct_only",
            "observation_date",
            postgresql_include=(
                "lowest_price_minor",
                "highest_price_minor",
                "price_sum_minor",
                "sample_count",
            ),
        ),
    )

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), primary_key=True
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    direct_only: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    lowest_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    highest_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    price_sum_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyTrendAggregateCoverage(Base):
    """Marks a query/day as rebuilt, including days with no detailed observations."""

    __tablename__ = "daily_trend_aggregate_coverage"

    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="CASCADE"), primary_key=True
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )

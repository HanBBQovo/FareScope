from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ExportStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ExportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_export_jobs_user_idempotency"),
        CheckConstraint("format IN ('csv', 'json')", name="valid_format"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'expired', 'deleting')",
            name="valid_status",
        ),
        CheckConstraint("range_start < range_end", name="valid_range"),
        CheckConstraint(
            "attempt >= 0 AND max_attempts >= 1 AND attempt <= max_attempts",
            name="valid_attempts",
        ),
        CheckConstraint("processed_rows >= 0", name="processed_rows_nonnegative"),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="row_count_nonnegative"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_bytes_nonnegative"),
        CheckConstraint("reserved_bytes >= 0", name="reserved_bytes_nonnegative"),
        Index("ix_export_jobs_user_created", "user_id", "created_at", "id"),
        Index(
            "ix_export_jobs_user_subscription_created",
            "user_id",
            "subscription_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_export_jobs_pending",
            "dispatch_published_at",
            "available_at",
            "dispatch_lease_expires_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_export_jobs_recovery",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_export_jobs_expiry",
            "expires_at",
            "id",
            postgresql_where=text("status = 'succeeded' AND expires_at IS NOT NULL"),
        ),
        Index(
            "ix_export_jobs_user_quota",
            "user_id",
            "status",
            postgresql_include=("file_name", "size_bytes", "reserved_bytes"),
        ),
        Index(
            "ix_export_jobs_active_reservations",
            "status",
            postgresql_include=("reserved_bytes", "range_start", "range_end"),
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_export_jobs_deleting",
            "updated_at",
            "id",
            postgresql_where=text("status = 'deleting'"),
        ),
        Index(
            "ix_export_jobs_referenced_files",
            "file_name",
            postgresql_where=text("file_name IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    search_query_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_queries.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ExportStatus.PENDING.value, nullable=False
    )
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dispatch_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    dispatch_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    file_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)


class ExportJobCollectionRun(Base):
    __tablename__ = "export_job_collection_runs"
    __table_args__ = (
        Index("ix_export_job_collection_runs_run", "collection_run_id", "export_job_id"),
    )

    export_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("export_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

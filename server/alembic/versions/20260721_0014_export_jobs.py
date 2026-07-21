"""add durable price export jobs

Revision ID: 20260721_0014
Revises: 20260721_0013
Create Date: 2026-07-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0014"
down_revision: str | None = "20260721_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("attempt", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), server_default="3", nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatch_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts >= 1 AND attempt <= max_attempts",
            name="ck_export_jobs_valid_attempts",
        ),
        sa.CheckConstraint("format IN ('csv', 'json')", name="ck_export_jobs_valid_format"),
        sa.CheckConstraint("processed_rows >= 0", name="ck_export_jobs_processed_rows_nonnegative"),
        sa.CheckConstraint("range_start < range_end", name="ck_export_jobs_valid_range"),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="ck_export_jobs_row_count_nonnegative",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_export_jobs_size_bytes_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_bytes >= 0",
            name="ck_export_jobs_reserved_bytes_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'expired', 'deleting')",
            name="ck_export_jobs_valid_status",
        ),
        sa.ForeignKeyConstraint(["search_query_id"], ["search_queries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_export_jobs_user_idempotency"),
    )
    op.create_table(
        "export_job_collection_runs",
        sa.Column("export_job_id", sa.Uuid(), nullable=False),
        sa.Column("collection_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["export_job_id"],
            ["export_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("export_job_id", "collection_run_id"),
    )
    op.create_index(
        "ix_export_job_collection_runs_run",
        "export_job_collection_runs",
        ["collection_run_id", "export_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_export_jobs_active_reservations",
        "export_jobs",
        ["status"],
        unique=False,
        postgresql_include=["reserved_bytes", "range_start", "range_end"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_export_jobs_deleting",
        "export_jobs",
        ["updated_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'deleting'"),
    )
    op.create_index(
        "ix_export_jobs_referenced_files",
        "export_jobs",
        ["file_name"],
        unique=False,
        postgresql_where=sa.text("file_name IS NOT NULL"),
    )
    op.create_index(
        "ix_export_jobs_expiry",
        "export_jobs",
        ["expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'succeeded' AND expires_at IS NOT NULL"),
    )
    op.create_index(
        "ix_export_jobs_pending",
        "export_jobs",
        ["dispatch_published_at", "available_at", "dispatch_lease_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_export_jobs_recovery",
        "export_jobs",
        ["lease_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_export_jobs_user_quota",
        "export_jobs",
        ["user_id", "status"],
        unique=False,
        postgresql_include=["file_name", "size_bytes", "reserved_bytes"],
    )
    op.create_index(
        "ix_export_jobs_user_created",
        "export_jobs",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_export_jobs_user_subscription_created",
        "export_jobs",
        ["user_id", "subscription_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_export_jobs_user_subscription_created", table_name="export_jobs")
    op.drop_index("ix_export_jobs_user_created", table_name="export_jobs")
    op.execute("DROP INDEX IF EXISTS ix_export_jobs_user_quota")
    op.execute("DROP INDEX IF EXISTS ix_export_jobs_referenced_files")
    op.execute("DROP INDEX IF EXISTS ix_export_jobs_deleting")
    op.execute("DROP INDEX IF EXISTS ix_export_jobs_active_reservations")
    op.drop_index("ix_export_jobs_recovery", table_name="export_jobs")
    op.drop_index("ix_export_jobs_pending", table_name="export_jobs")
    op.drop_index("ix_export_jobs_expiry", table_name="export_jobs")
    op.execute("DROP INDEX IF EXISTS ix_export_job_collection_runs_run")
    op.execute("DROP TABLE IF EXISTS export_job_collection_runs")
    op.drop_table("export_jobs")

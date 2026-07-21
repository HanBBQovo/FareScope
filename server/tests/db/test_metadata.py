from sqlalchemy import DateTime, Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import app.models  # noqa: F401
from app.db.base import Base


def test_expected_persistence_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "alert_events",
        "alert_rule_channels",
        "alert_rules",
        "audit_events",
        "calendar_price_observations",
        "collection_artifacts",
        "collection_runs",
        "daily_price_aggregates",
        "daily_trend_aggregate_coverage",
        "daily_trend_aggregates",
        "export_job_collection_runs",
        "export_jobs",
        "fare_offers",
        "itineraries",
        "latest_calendar_price_snapshots",
        "latest_price_snapshots",
        "notification_channels",
        "notification_deliveries",
        "price_observations",
        "providers",
        "schema_observations",
        "search_legs",
        "search_queries",
        "segments",
        "sessions",
        "subscription_filters",
        "subscriptions",
        "users",
    }


def test_user_owned_resources_and_shared_queries_are_constrained() -> None:
    search_queries = Base.metadata.tables["search_queries"]
    query_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in search_queries.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("query_hash",) in query_unique_columns

    for table_name in ("subscriptions", "notification_channels", "alert_rules", "export_jobs"):
        table = Base.metadata.tables[table_name]
        user_foreign_key = next(iter(table.c.user_id.foreign_keys))
        assert user_foreign_key.target_fullname == "users.id"


def test_security_tables_store_hashes_or_ciphertext_not_raw_secrets() -> None:
    sessions = Base.metadata.tables["sessions"]
    channels = Base.metadata.tables["notification_channels"]

    assert "token_hash" in sessions.c and "token" not in sessions.c
    assert "secret_ciphertext" in channels.c and "secret" not in channels.c


def test_notification_channels_store_delivery_schedule_without_plaintext_destination() -> None:
    channels = Base.metadata.tables["notification_channels"]

    assert {
        "timezone",
        "quiet_hours_start",
        "quiet_hours_end",
        "allowed_weekdays",
    }.issubset(channels.c.keys())
    assert channels.c.timezone.nullable is True
    assert channels.c.quiet_hours_start.nullable is True
    assert channels.c.allowed_weekdays.nullable is True


def test_user_identity_is_username_based_and_email_is_optional_contact() -> None:
    users = Base.metadata.tables["users"]

    assert "username" in users.c
    assert "normalized_username" in users.c
    assert users.c.email.nullable is True
    assert "normalized_email" not in users.c
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in users.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("normalized_username",) in unique_columns
    # Existing display names remain a presentation concern; the migration only
    # backfills the new login identity and does not overwrite user-facing text.
    assert users.c.display_name.nullable is False


def test_price_observations_use_utc_partition_key_and_minor_units() -> None:
    observations = Base.metadata.tables["price_observations"]

    assert observations.dialect_options["postgresql"]["partition_by"] == "RANGE (observed_at)"
    assert tuple(observations.primary_key.columns.keys()) == ("id", "observed_at")
    assert isinstance(observations.c.observed_at.type, DateTime)
    assert observations.c.observed_at.type.timezone is True
    assert isinstance(observations.c.total_price_minor.type, Integer)

    ddl = str(CreateTable(observations).compile(dialect=postgresql.dialect()))
    assert "PARTITION BY RANGE (observed_at)" in ddl


def test_daily_trend_aggregates_preserve_exact_weighted_statistics() -> None:
    aggregates = Base.metadata.tables["daily_trend_aggregates"]
    coverage = Base.metadata.tables["daily_trend_aggregate_coverage"]

    assert tuple(aggregates.primary_key.columns.keys()) == (
        "search_query_id",
        "observation_date",
        "currency",
        "direct_only",
    )
    assert isinstance(aggregates.c.lowest_price_minor.type, Integer)
    assert aggregates.c.price_sum_minor.type.python_type is int
    assert aggregates.c.sample_count.type.python_type is int
    assert coverage.c.source_last_observed_at.type.timezone is True

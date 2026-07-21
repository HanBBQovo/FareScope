from dataclasses import dataclass

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class HotQueryContract:
    name: str
    table: str
    maximum_rows: int
    keyset_columns: tuple[str, ...]


HOT_QUERY_CONTRACTS = (
    HotQueryContract(
        name="user-subscriptions",
        table="subscriptions",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("created_at", "id"),
    ),
    HotQueryContract(
        name="due-subscriptions",
        table="subscriptions",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("next_due_at", "id"),
    ),
    HotQueryContract(
        name="fare-search-offers",
        table="fare_offers",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("total_price_minor", "id"),
    ),
    HotQueryContract(
        name="calendar-date-matrix",
        table="latest_calendar_price_snapshots",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("departure_date", "return_date"),
    ),
    HotQueryContract(
        name="price-history",
        table="price_observations",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("observed_at", "collection_run_id"),
    ),
    HotQueryContract(
        name="collection-run-list",
        table="collection_runs",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("scheduled_at", "id"),
    ),
    HotQueryContract(
        name="collection-lease-pending",
        table="collection_runs",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("scheduled_at", "id"),
    ),
    HotQueryContract(
        name="collection-lease-recovery",
        table="collection_runs",
        maximum_rows=MAX_PAGE_SIZE,
        keyset_columns=("lease_expires_at", "id"),
    ),
)


CRITICAL_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    "subscriptions": {
        "ix_subscriptions_user_enabled": ("user_id", "enabled"),
        "ix_subscriptions_due": ("enabled", "next_due_at"),
        "ix_subscriptions_user_created": ("user_id", "created_at", "id"),
        "ix_subscriptions_due_keyset": ("next_due_at", "id"),
    },
    "search_legs": {
        "ix_search_legs_route_date": (
            "origin_code",
            "destination_code",
            "departure_date",
        ),
        "ix_search_legs_first_route_date": (
            "origin_code",
            "destination_code",
            "departure_date",
            "search_query_id",
        ),
    },
    "collection_runs": {
        "ix_collection_runs_query_scheduled": ("search_query_id", "scheduled_at"),
        "ix_collection_runs_status_lease": ("status", "lease_expires_at"),
        "ix_collection_runs_pending_keyset": ("scheduled_at", "id"),
        "ix_collection_runs_recovery_keyset": ("lease_expires_at", "id"),
        "ix_collection_runs_query_success_finished": (
            "search_query_id",
            "finished_at",
            "id",
        ),
        "ix_collection_runs_query_terminal_finished": (
            "search_query_id",
            "finished_at",
            "id",
        ),
    },
    "fare_offers": {
        "ix_fare_offers_run_price": (
            "collection_run_id",
            "total_price_minor",
            "id",
        ),
    },
    "price_observations": {
        "ix_price_observations_search_observed": ("search_query_id", "observed_at"),
        "ix_price_observations_query_run_price": (
            "search_query_id",
            "observed_at",
            "collection_run_id",
        ),
    },
    "latest_price_snapshots": {
        "ix_latest_price_snapshots_price": (
            "currency",
            "is_direct",
            "total_price_minor",
            "search_query_id",
        ),
    },
    "calendar_price_observations": {
        "ix_calendar_price_observations_search_dates": (
            "search_query_id",
            "departure_date",
            "return_date",
            "observed_at",
        ),
        "ix_calendar_price_observations_history_keyset": (
            "search_query_id",
            "observed_at",
            "id",
        ),
    },
    "latest_calendar_price_snapshots": {
        "ix_latest_calendar_price_snapshots_search_dates": (
            "search_query_id",
            "departure_date",
            "return_date",
            "observed_at",
        ),
    },
}

PARTITIONED_TABLES = {
    "calendar_price_observations": "observed_at",
    "price_observations": "observed_at",
}

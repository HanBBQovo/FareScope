from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_prefix="FARESCOPE_",
        extra="ignore",
    )

    app_name: str = "FareScope"
    environment: str = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+asyncpg://farescope:farescope@127.0.0.1:5432/farescope"
    redis_url: str = "redis://127.0.0.1:6379/0"
    cors_origins: list[str] = ["http://localhost:5278"]
    database_echo: bool = False
    database_pool_size: int = Field(default=8, ge=1, le=50)
    database_max_overflow: int = Field(default=4, ge=0, le=50)
    database_pool_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    database_statement_timeout_ms: int = Field(default=10000, ge=100, le=300000)

    bootstrap_admin_token: SecretStr | None = None
    secret_encryption_key: SecretStr | None = None
    session_cookie_name: str = "farescope_session"
    csrf_cookie_name: str = "farescope_csrf"
    session_cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=31_536_000)
    public_registration_enabled: bool = True
    notification_delivery_batch_size: int = Field(default=50, ge=1, le=200)
    notification_delivery_max_attempts: int = Field(default=5, ge=1, le=20)
    notification_retry_base_seconds: int = Field(default=60, ge=5, le=3600)
    notification_delivery_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    notification_delivery_stale_seconds: int = Field(default=900, ge=60, le=86_400)
    collector_proxy_server: str | None = None
    collector_browser_channel: Literal["chrome"] | None = None
    collection_screenshot_directory: str | None = None
    collection_dispatch_lease_seconds: int = Field(default=120, ge=30, le=3600)
    collection_run_lease_seconds: int = Field(default=900, ge=60, le=7200)
    collection_retry_base_seconds: int = Field(default=60, ge=5, le=3600)
    collection_retry_max_seconds: int = Field(default=1800, ge=30, le=21600)
    collection_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    collection_coordination_backend: Literal["local", "redis"] = "local"
    collection_coordination_lease_ttl_seconds: float = Field(default=180, ge=150, le=3600)
    collection_coordination_acquire_timeout_seconds: float = Field(
        default=120,
        ge=1,
        le=3600,
    )
    collection_coordination_poll_interval_seconds: float = Field(default=0.5, ge=0.05, le=10)
    collection_coordination_redis_timeout_seconds: float = Field(default=2, ge=0.1, le=30)
    collection_coordination_key_prefix: str = Field(
        default="farescope:collection-coordination",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    collection_provider_concurrency: int = Field(default=2, ge=1, le=32)
    collection_route_concurrency: int = Field(default=1, ge=1, le=8)
    collection_minimum_interval_seconds: float = Field(default=3.0, ge=0, le=3600)
    collection_jitter_seconds: float = Field(default=1.0, ge=0, le=3600)
    collection_capture_settle_seconds: float = Field(default=2.0, ge=0, le=30)
    collection_scheduler_tick_seconds: int = Field(default=30, ge=5, le=3600)
    collection_scheduler_subscription_batch_size: int = Field(default=500, ge=1, le=5000)
    collection_scheduler_dispatch_batch_size: int = Field(default=100, ge=1, le=1000)
    collection_schedule_bucket_seconds: int = Field(default=300, ge=30, le=86400)
    collection_partition_maintenance_seconds: int = Field(default=3600, ge=300, le=86400)
    collection_partition_archive_after_months: int | None = Field(
        default=24,
        ge=3,
        le=1200,
    )
    collection_partition_purge_after_months: int | None = Field(
        default=None,
        ge=4,
        le=2400,
    )
    collection_partition_max_actions: int = Field(default=2, ge=1, le=24)
    collection_realtime_stream_key: str = Field(
        default="farescope:realtime:collection-runs",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    collection_realtime_stream_max_length: int = Field(default=20_000, ge=1_000, le=1_000_000)
    collection_realtime_snapshot_limit: int = Field(default=100, ge=1, le=500)
    collection_realtime_read_count: int = Field(default=100, ge=1, le=500)
    collection_realtime_block_ms: int = Field(default=15_000, ge=1_000, le=30_000)
    collection_realtime_connection_seconds: int = Field(default=300, ge=30, le=3_600)
    collection_realtime_redis_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30)
    collection_realtime_retry_ms: int = Field(default=2_000, ge=500, le=60_000)
    export_directory: str = "/var/lib/farescope/exports"
    export_max_range_days: int = Field(default=366, ge=1, le=3660)
    export_max_rows: int = Field(default=250_000, ge=1_000, le=2_000_000)
    export_max_file_bytes: int = Field(default=134_217_728, ge=1_048_576, le=1_073_741_824)
    export_page_size: int = Field(default=2_000, ge=100, le=10_000)
    export_file_ttl_seconds: int = Field(default=604_800, ge=3600, le=31_536_000)
    export_lease_seconds: int = Field(default=900, ge=60, le=7200)
    export_max_attempts: int = Field(default=3, ge=1, le=10)
    export_retry_base_seconds: int = Field(default=60, ge=5, le=3600)
    export_dispatch_batch_size: int = Field(default=20, ge=1, le=100)
    export_dispatch_lease_seconds: int = Field(default=300, ge=30, le=3600)
    export_pending_timeout_seconds: int = Field(default=86_400, ge=3600, le=604_800)
    export_max_active_jobs: int = Field(default=5, ge=1, le=50)
    export_global_max_active_jobs: int = Field(default=100, ge=1, le=100_000)
    export_manifest_max_runs: int = Field(default=20_000, ge=1000, le=1_000_000)
    export_user_max_retained_files: int = Field(default=20, ge=1, le=1000)
    export_user_max_retained_bytes: int = Field(
        default=1_073_741_824,
        ge=1_048_576,
        le=109_951_162_777_600,
    )
    export_min_free_bytes: int = Field(
        default=2_147_483_648,
        ge=0,
        le=109_951_162_777_600,
    )
    export_min_free_ratio: float = Field(default=0.1, ge=0, le=0.9)
    export_orphan_grace_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    export_orphan_cleanup_batch_size: int = Field(default=100, ge=1, le=10_000)

    @field_validator("collector_browser_channel", mode="before")
    @classmethod
    def normalize_collector_browser_channel(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = str(value).strip().casefold()
        if normalized in {"", "chromium"}:
            return None
        return normalized

    @field_validator(
        "collection_partition_archive_after_months",
        "collection_partition_purge_after_months",
        mode="before",
    )
    @classmethod
    def normalize_optional_partition_retention(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().casefold() in {"", "0", "none", "off"}:
            return None
        if value == 0:
            return None
        return value

    @model_validator(mode="after")
    def validate_production_cookies(self) -> "Settings":
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("secure session cookies are required in production")
        if self.environment == "production" and self.secret_encryption_key is None:
            raise ValueError("a secret encryption key is required in production")
        if self.environment == "production" and self.collection_coordination_backend != "redis":
            raise ValueError("Redis collection coordination is required in production")
        if self.export_user_max_retained_bytes < self.export_max_file_bytes:
            raise ValueError("per-user export storage must allow at least one maximum-size file")
        if self.export_user_max_retained_files < self.export_max_active_jobs:
            raise ValueError(
                "per-user retained export files must cover all active export jobs"
            )
        if self.export_pending_timeout_seconds <= self.export_dispatch_lease_seconds:
            raise ValueError("export pending timeout must exceed the dispatch lease")
        if self.export_orphan_grace_seconds < self.export_lease_seconds * 2:
            raise ValueError("export orphan grace must be at least twice the export lease")
        if (
            self.collection_coordination_backend == "redis"
            and self.collection_coordination_acquire_timeout_seconds
            >= self.collection_run_lease_seconds
        ):
            raise ValueError(
                "collection coordination acquire timeout must be shorter than the run lease"
            )
        if self.collection_partition_purge_after_months is not None:
            if self.collection_partition_archive_after_months is None:
                raise ValueError("partition purging requires archiving to be enabled")
            if (
                self.collection_partition_purge_after_months
                <= self.collection_partition_archive_after_months
            ):
                raise ValueError("partition purge retention must exceed archive retention")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

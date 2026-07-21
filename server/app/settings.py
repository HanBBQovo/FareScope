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

    @field_validator("collector_browser_channel", mode="before")
    @classmethod
    def normalize_collector_browser_channel(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = str(value).strip().casefold()
        if normalized in {"", "chromium"}:
            return None
        return normalized

    @model_validator(mode="after")
    def validate_production_cookies(self) -> "Settings":
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("secure session cookies are required in production")
        if self.environment == "production" and self.secret_encryption_key is None:
            raise ValueError("a secret encryption key is required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

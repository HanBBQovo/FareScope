from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()

from unittest.mock import patch

from app.db.session import create_engine


def test_postgres_engine_has_bounded_pool_and_statement_timeout() -> None:
    with patch("app.db.session.create_async_engine") as create_async_engine:
        create_engine(
            "postgresql+asyncpg://user:password@database/farescope",
            pool_size=8,
            max_overflow=4,
            pool_timeout_seconds=2.0,
            pool_recycle_seconds=1800,
            statement_timeout_ms=10000,
            application_name="farescope-api",
        )

    create_async_engine.assert_called_once_with(
        "postgresql+asyncpg://user:password@database/farescope",
        echo=False,
        pool_pre_ping=True,
        pool_size=8,
        max_overflow=4,
        pool_timeout=2.0,
        pool_recycle=1800,
        connect_args={
            "server_settings": {
                "application_name": "farescope-api",
                "statement_timeout": "10000",
            }
        },
    )

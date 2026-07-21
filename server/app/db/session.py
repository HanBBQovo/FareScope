from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = 8,
    max_overflow: int = 4,
    pool_timeout_seconds: float = 2.0,
    pool_recycle_seconds: int = 1800,
    statement_timeout_ms: int = 10000,
    application_name: str = "farescope",
) -> AsyncEngine:
    options: dict[str, object] = {
        "echo": echo,
        "pool_pre_ping": True,
    }
    if database_url.startswith("postgresql"):
        options.update(
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
            pool_recycle=pool_recycle_seconds,
            connect_args={
                "server_settings": {
                    "application_name": application_name,
                    "statement_timeout": str(statement_timeout_ms),
                }
            },
        )
    return create_async_engine(database_url, **options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session, session.begin():
        yield session

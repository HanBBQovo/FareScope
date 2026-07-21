from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.engine import make_url

DISPOSABLE_CONFIRMATION = "I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE"
PERFORMANCE_DATABASE_PREFIX = "farescope_perf_"
_DATABASE_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")


def require_confirmation(value: str) -> None:
    if value != DISPOSABLE_CONFIRMATION:
        raise ValueError(
            "confirmation does not match; refusing to operate on a performance database"
        )


def validate_database_name(name: str) -> str:
    if not _DATABASE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "database name must contain only lowercase letters, digits, and underscores"
        )
    if not name.startswith(PERFORMANCE_DATABASE_PREFIX):
        raise ValueError(
            f"database name must start with {PERFORMANCE_DATABASE_PREFIX!r}; got {name!r}"
        )
    return name


def validate_performance_database_url(value: str) -> str:
    url = make_url(value)
    if not url.database:
        raise ValueError("database URL must include a database name")
    validate_database_name(url.database)
    return value


def to_asyncpg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def to_sqlalchemy_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def redact_url(value: str) -> str:
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.username is None:
        return value
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    user = parsed.username
    netloc = f"{user}:***@{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

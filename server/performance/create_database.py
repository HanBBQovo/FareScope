from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

from performance.safety import (
    DISPOSABLE_CONFIRMATION,
    redact_url,
    require_confirmation,
    to_asyncpg_url,
    validate_database_name,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one explicitly named disposable FareScope performance database."
    )
    parser.add_argument("database_name", help="Must start with farescope_perf_.")
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Required exact value: {DISPOSABLE_CONFIRMATION}",
    )
    return parser.parse_args()


async def _create_database(*, admin_url: str, database_name: str) -> None:
    connection = await asyncpg.connect(to_asyncpg_url(admin_url))
    try:
        existing = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            database_name,
        )
        if existing:
            raise RuntimeError(
                f"database {database_name!r} already exists; choose a new explicit name"
            )
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def main() -> None:
    arguments = _arguments()
    require_confirmation(arguments.confirm)
    database_name = validate_database_name(arguments.database_name)
    admin_url = os.environ["FARESCOPE_PERF_ADMIN_URL"]
    await _create_database(admin_url=admin_url, database_name=database_name)
    print(f"created disposable database {database_name}")
    print(f"admin server: {redact_url(admin_url)}")


if __name__ == "__main__":
    asyncio.run(main())

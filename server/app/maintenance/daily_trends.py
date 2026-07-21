from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db import create_engine, create_session_factory
from app.services.daily_trends import (
    DailyTrendSourceUnavailableError,
    maintain_daily_trend_aggregates,
)
from app.settings import get_settings

_CHECKPOINT_VERSION = 1


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild UTC-day Dashboard trend aggregates with bounded keyset batches."
    )
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=1_000)
    parser.add_argument("--search-query-id", type=UUID)
    parser.add_argument("--after-date", type=date.fromisoformat)
    parser.add_argument("--after-search-query-id", type=UUID)
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        help="Atomically persist and automatically resume the last committed keyset cursor.",
    )
    return parser.parse_args()


def _scope_payload(
    *,
    start_date: date,
    end_date: date,
    search_query_id: UUID | None,
) -> dict[str, str | None]:
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "search_query_id": str(search_query_id) if search_query_id is not None else None,
    }


def _cursor_payload(cursor: tuple[date, UUID] | None) -> dict[str, str] | None:
    if cursor is None:
        return None
    return {
        "observation_date": cursor[0].isoformat(),
        "search_query_id": str(cursor[1]),
    }


def _checkpoint_payload(
    *,
    scope: dict[str, str | None],
    cursor: tuple[date, UUID] | None,
    batches_committed: int,
    days_refreshed: int,
    aggregates_written: int,
    complete: bool,
) -> dict[str, object]:
    return {
        "version": _CHECKPOINT_VERSION,
        "scope": scope,
        "next_cursor": _cursor_payload(cursor),
        "batches_committed": batches_committed,
        "days_refreshed": days_refreshed,
        "aggregates_written": aggregates_written,
        "complete": complete,
    }


def _write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _load_checkpoint(
    path: Path,
    *,
    expected_scope: dict[str, str | None],
) -> tuple[tuple[date, UUID] | None, int, int, int, bool] | None:
    if not path.exists():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"daily trend checkpoint is unreadable: {path}") from error
    if payload.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("daily trend checkpoint version is unsupported")
    if payload.get("scope") != expected_scope:
        raise ValueError("daily trend checkpoint scope does not match this invocation")
    raw_cursor = payload.get("next_cursor")
    cursor = None
    if raw_cursor is not None:
        if not isinstance(raw_cursor, dict):
            raise ValueError("daily trend checkpoint cursor is invalid")
        try:
            cursor = (
                date.fromisoformat(str(raw_cursor["observation_date"])),
                UUID(str(raw_cursor["search_query_id"])),
            )
        except (KeyError, ValueError) as error:
            raise ValueError("daily trend checkpoint cursor is invalid") from error
    try:
        batches_committed = int(payload["batches_committed"])
        days_refreshed = int(payload["days_refreshed"])
        aggregates_written = int(payload["aggregates_written"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("daily trend checkpoint counters are invalid") from error
    complete = payload.get("complete")
    if not isinstance(complete, bool) or min(
        batches_committed,
        days_refreshed,
        aggregates_written,
    ) < 0:
        raise ValueError("daily trend checkpoint state is invalid")
    return cursor, batches_committed, days_refreshed, aggregates_written, complete


def _emit_jsonl(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


async def _run(
    arguments: argparse.Namespace,
    *,
    emit: Callable[[dict[str, object]], None] = _emit_jsonl,
) -> dict[str, object]:
    if arguments.days < 1:
        raise ValueError("days must be positive")
    if arguments.max_batches < 1:
        raise ValueError("max-batches must be positive")
    if (arguments.after_date is None) != (arguments.after_search_query_id is None):
        raise ValueError("after-date and after-search-query-id must be provided together")
    if arguments.checkpoint_file is not None and arguments.after_date is not None:
        raise ValueError("manual after cursor cannot be combined with a checkpoint file")
    today = datetime.now(UTC).date()
    end_date = arguments.end_date or today
    start_date = arguments.start_date or end_date - timedelta(days=arguments.days - 1)
    if end_date > today:
        raise ValueError("end-date cannot be in the future")

    scope = _scope_payload(
        start_date=start_date,
        end_date=end_date,
        search_query_id=arguments.search_query_id,
    )
    checkpoint = (
        _load_checkpoint(arguments.checkpoint_file, expected_scope=scope)
        if arguments.checkpoint_file is not None
        else None
    )
    if checkpoint is not None:
        cursor, batches, days_refreshed, aggregates_written, exhausted = checkpoint
    else:
        cursor = (
            (arguments.after_date, arguments.after_search_query_id)
            if arguments.after_date is not None and arguments.after_search_query_id is not None
            else None
        )
        batches = 0
        days_refreshed = 0
        aggregates_written = 0
        exhausted = False
    if exhausted:
        return _checkpoint_payload(
            scope=scope,
            cursor=cursor,
            batches_committed=batches,
            days_refreshed=days_refreshed,
            aggregates_written=aggregates_written,
            complete=True,
        )

    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        pool_recycle_seconds=settings.database_pool_recycle_seconds,
        statement_timeout_ms=max(settings.database_statement_timeout_ms, 60_000),
        application_name="farescope-daily-trend-maintenance",
    )
    factory = create_session_factory(engine)
    invocation_batches = 0
    try:
        for _ in range(arguments.max_batches):
            async with factory() as session, session.begin():
                result = await maintain_daily_trend_aggregates(
                    session,
                    start_date=start_date,
                    end_date=end_date,
                    batch_size=arguments.batch_size,
                    search_query_id=arguments.search_query_id,
                    after=cursor,
                )
            batches += 1
            invocation_batches += 1
            days_refreshed += result.day_count
            aggregates_written += result.aggregate_count
            cursor = result.next_cursor
            if result.day_count < arguments.batch_size:
                exhausted = True
            state = _checkpoint_payload(
                scope=scope,
                cursor=cursor,
                batches_committed=batches,
                days_refreshed=days_refreshed,
                aggregates_written=aggregates_written,
                complete=exhausted,
            )
            if arguments.checkpoint_file is not None:
                _write_checkpoint(arguments.checkpoint_file, state)
            emit(
                {
                    "event": "batch_committed",
                    "invocation_batch": invocation_batches,
                    "batch_days_refreshed": result.day_count,
                    "batch_aggregates_written": result.aggregate_count,
                    **state,
                }
            )
            if exhausted:
                break
    except DailyTrendSourceUnavailableError as error:
        blocked = {
            "event": "blocked_source_unavailable",
            **_checkpoint_payload(
                scope=scope,
                cursor=cursor,
                batches_committed=batches,
                days_refreshed=days_refreshed,
                aggregates_written=aggregates_written,
                complete=False,
            ),
            "archived_partitions": error.archived_partitions,
            "missing_hot_partitions": error.missing_hot_partitions,
            "message": str(error),
        }
        if arguments.checkpoint_file is not None:
            _write_checkpoint(arguments.checkpoint_file, blocked)
        emit(blocked)
        raise
    finally:
        await engine.dispose()
    return {
        **_checkpoint_payload(
            scope=scope,
            cursor=cursor,
            batches_committed=batches,
            days_refreshed=days_refreshed,
            aggregates_written=aggregates_written,
            complete=exhausted,
        ),
        "invocation_batches": invocation_batches,
    }


def main() -> None:
    try:
        result = asyncio.run(_run(_arguments()))
    except DailyTrendSourceUnavailableError:
        raise SystemExit(2) from None
    _emit_jsonl({"event": "summary", **result})


if __name__ == "__main__":
    main()

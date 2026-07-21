from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.tasks import scheduler as scheduler_tasks


class _TransactionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


class _SessionContext:
    def __init__(self) -> None:
        self.connection_value = object()

    async def __aenter__(self) -> _SessionContext:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None

    def begin(self) -> _TransactionContext:
        return _TransactionContext()

    async def connection(self) -> object:
        return self.connection_value


@pytest.mark.asyncio
async def test_partition_task_uses_lock_and_maintains_both_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionContext()
    try_lock = AsyncMock(return_value=True)
    ensure_partitions = AsyncMock(
        return_value={
            "price_observations": ("price_a", "price_b"),
            "calendar_price_observations": ("calendar_a", "calendar_b"),
        }
    )
    monkeypatch.setattr(scheduler_tasks, "_try_transaction_lock", try_lock)
    monkeypatch.setattr(
        scheduler_tasks,
        "ensure_all_observation_partitions",
        ensure_partitions,
    )

    result = await scheduler_tasks.maintain_observation_partitions(
        session_factory=lambda: session,  # type: ignore[arg-type]
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert result["status"] == "ok"
    assert result["partition_count"] == 4
    try_lock.assert_awaited_once_with(session, scheduler_tasks._PARTITION_LOCK_ID)
    ensure_partitions.assert_awaited_once_with(
        session.connection_value,
        reference=datetime(2026, 7, 20, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_partition_task_skips_when_another_instance_holds_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionContext()
    monkeypatch.setattr(
        scheduler_tasks,
        "_try_transaction_lock",
        AsyncMock(return_value=False),
    )
    ensure_partitions = AsyncMock()
    monkeypatch.setattr(
        scheduler_tasks,
        "ensure_all_observation_partitions",
        ensure_partitions,
    )

    result = await scheduler_tasks.maintain_observation_partitions(
        session_factory=lambda: session,  # type: ignore[arg-type]
    )

    assert result == {"status": "skipped", "reason": "partition_lock_busy"}
    ensure_partitions.assert_not_awaited()

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from app.db.partitions import PartitionLifecycleAction
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
    maintain_lifecycle = AsyncMock(
        return_value=(
            PartitionLifecycleAction(
                action="archive",
                parent_table="price_observations",
                partition_name="price_observations_y2024m01",
                partition_month=date(2024, 1, 1),
            ),
        )
    )
    monkeypatch.setattr(scheduler_tasks, "_try_transaction_lock", try_lock)
    monkeypatch.setattr(
        scheduler_tasks,
        "ensure_all_observation_partitions",
        ensure_partitions,
    )
    monkeypatch.setattr(
        scheduler_tasks,
        "maintain_observation_partition_lifecycle",
        maintain_lifecycle,
    )

    result = await scheduler_tasks.maintain_observation_partitions(
        session_factory=lambda: session,  # type: ignore[arg-type]
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert result["status"] == "ok"
    assert result["partition_count"] == 4
    assert result["lifecycle_actions"] == [
        {
            "action": "archive",
            "table": "price_observations",
            "partition": "price_observations_y2024m01",
            "month": "2024-01-01",
        }
    ]
    try_lock.assert_awaited_once_with(session, scheduler_tasks._PARTITION_LOCK_ID)
    ensure_partitions.assert_awaited_once_with(
        session.connection_value,
        reference=datetime(2026, 7, 20, tzinfo=UTC),
    )
    maintain_lifecycle.assert_awaited_once_with(
        session.connection_value,
        reference=datetime(2026, 7, 20, tzinfo=UTC),
        archive_after_months=24,
        purge_after_months=None,
        max_actions=2,
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
    maintain_lifecycle = AsyncMock()
    monkeypatch.setattr(
        scheduler_tasks,
        "ensure_all_observation_partitions",
        ensure_partitions,
    )
    monkeypatch.setattr(
        scheduler_tasks,
        "maintain_observation_partition_lifecycle",
        maintain_lifecycle,
    )

    result = await scheduler_tasks.maintain_observation_partitions(
        session_factory=lambda: session,  # type: ignore[arg-type]
    )

    assert result == {"status": "skipped", "reason": "partition_lock_busy"}
    ensure_partitions.assert_not_awaited()
    maintain_lifecycle.assert_not_awaited()

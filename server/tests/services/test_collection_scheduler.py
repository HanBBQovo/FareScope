from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import CollectionRun
from app.models.enums import CollectionStatus
from app.services.collection_scheduler import advance_due_at, scheduled_idempotency_key
from app.tasks.collection import _assert_owned_lease, _schedule_retry_if_eligible


def test_advance_due_at_skips_missed_periods_without_scheduler_drift() -> None:
    previous = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    now = previous + timedelta(minutes=31)

    assert advance_due_at(
        previous,
        poll_interval_seconds=900,
        now=now,
    ) == previous + timedelta(minutes=45)


def test_scheduled_idempotency_key_is_stable_within_a_bucket() -> None:
    query_id = uuid4()
    first = datetime(2026, 7, 20, 0, 0, 1, tzinfo=UTC)
    second = datetime(2026, 7, 20, 0, 4, 59, tzinfo=UTC)

    assert scheduled_idempotency_key(
        search_query_id=query_id,
        now=first,
        bucket_seconds=300,
    ) == scheduled_idempotency_key(
        search_query_id=query_id,
        now=second,
        bucket_seconds=300,
    )


def test_retryable_failure_returns_run_to_pending_with_bounded_backoff() -> None:
    failed_at = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    run = CollectionRun(
        search_query_id=uuid4(),
        provider_id=uuid4(),
        idempotency_key=f"test:{uuid4()}",
        status=CollectionStatus.FAILED.value,
        attempt=2,
        max_attempts=3,
        scheduled_at=failed_at,
        run_metadata={},
    )

    retry_at = _schedule_retry_if_eligible(
        run,
        retryable=True,
        failed_at=failed_at,
        base_seconds=60,
        maximum_seconds=90,
    )

    assert retry_at == failed_at + timedelta(seconds=90)
    assert run.status == CollectionStatus.PENDING.value
    assert run.scheduled_at == retry_at
    assert run.run_metadata["retry"]["scheduled_count"] == 1


def test_retryable_failure_adds_bounded_jitter_when_configured() -> None:
    failed_at = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    run = CollectionRun(
        search_query_id=uuid4(),
        provider_id=uuid4(),
        idempotency_key=f"test:{uuid4()}",
        status=CollectionStatus.FAILED.value,
        attempt=1,
        max_attempts=3,
        scheduled_at=failed_at,
        run_metadata={},
    )

    retry_at = _schedule_retry_if_eligible(
        run,
        retryable=True,
        failed_at=failed_at,
        base_seconds=60,
        maximum_seconds=180,
        jitter_ratio=0.5,
        random_fraction=1,
    )

    assert retry_at == failed_at + timedelta(seconds=90)
    assert run.run_metadata["retry"]["jitter_ratio"] == 0.5


def test_expired_collection_lease_cannot_commit_a_late_result() -> None:
    run = CollectionRun(
        search_query_id=uuid4(),
        provider_id=uuid4(),
        idempotency_key=f"test:{uuid4()}",
        status=CollectionStatus.RUNNING.value,
        attempt=1,
        max_attempts=3,
        scheduled_at=datetime(2026, 7, 20, tzinfo=UTC),
        lease_owner="worker:collector-a",
        lease_expires_at=datetime(2026, 7, 20, 0, 1, tzinfo=UTC),
        run_metadata={},
    )

    with pytest.raises(RuntimeError, match="lease"):
        _assert_owned_lease(
            run,
            "worker:collector-a",
            now=datetime(2026, 7, 20, 0, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize("retryable,attempt", [(False, 1), (True, 3)])
def test_nonretryable_or_exhausted_failure_stays_terminal(
    retryable: bool,
    attempt: int,
) -> None:
    failed_at = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    run = CollectionRun(
        search_query_id=uuid4(),
        provider_id=uuid4(),
        idempotency_key=f"test:{uuid4()}",
        status=CollectionStatus.FAILED.value,
        attempt=attempt,
        max_attempts=3,
        scheduled_at=failed_at,
        run_metadata={},
    )

    assert (
        _schedule_retry_if_eligible(
            run,
            retryable=retryable,
            failed_at=failed_at,
            base_seconds=60,
            maximum_seconds=600,
        )
        is None
    )
    assert run.status == CollectionStatus.FAILED.value

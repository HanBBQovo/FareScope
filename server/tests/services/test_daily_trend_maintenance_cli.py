from datetime import date
from uuid import uuid4

import pytest

from app.maintenance.daily_trends import (
    _checkpoint_payload,
    _load_checkpoint,
    _scope_payload,
    _write_checkpoint,
)


def test_atomic_checkpoint_round_trip_preserves_committed_cursor(tmp_path) -> None:
    query_id = uuid4()
    cursor_query_id = uuid4()
    scope = _scope_payload(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 7, 21),
        search_query_id=query_id,
    )
    payload = _checkpoint_payload(
        scope=scope,
        cursor=(date(2026, 6, 10), cursor_query_id),
        batches_committed=12,
        days_refreshed=6_000,
        aggregates_written=2_400,
        complete=False,
    )
    checkpoint_path = tmp_path / "daily-trends.json"

    _write_checkpoint(checkpoint_path, payload)

    assert _load_checkpoint(checkpoint_path, expected_scope=scope) == (
        (date(2026, 6, 10), cursor_query_id),
        12,
        6_000,
        2_400,
        False,
    )
    assert list(tmp_path.iterdir()) == [checkpoint_path]


def test_checkpoint_rejects_a_different_maintenance_scope(tmp_path) -> None:
    scope = _scope_payload(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 7, 21),
        search_query_id=None,
    )
    checkpoint_path = tmp_path / "daily-trends.json"
    _write_checkpoint(
        checkpoint_path,
        _checkpoint_payload(
            scope=scope,
            cursor=None,
            batches_committed=0,
            days_refreshed=0,
            aggregates_written=0,
            complete=False,
        ),
    )

    with pytest.raises(ValueError, match="scope does not match"):
        _load_checkpoint(
            checkpoint_path,
            expected_scope={**scope, "end_date": "2026-07-20"},
        )

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.export_data import archived_partition_overlaps
from app.services.export_files import (
    export_file_name,
    open_export_file,
    remove_export_file,
    resolve_export_file_path,
    sanitize_csv_cell,
)
from app.services.export_jobs import (
    ExportJobError,
    export_request_fingerprint,
    validate_export_range,
)
from app.tasks.exports import enqueue_export_job


def test_export_range_is_utc_bounded_and_idempotency_is_offset_stable() -> None:
    start, end = validate_export_range(
        datetime.fromisoformat("2026-07-01T08:00:00+08:00"),
        datetime.fromisoformat("2026-07-02T08:00:00+08:00"),
        max_range_days=1,
    )
    assert start == datetime(2026, 7, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 2, tzinfo=UTC)
    subscription_id = uuid4()
    first = export_request_fingerprint(
        subscription_id=subscription_id,
        export_format="json",
        range_start=start,
        range_end=end,
    )
    second = export_request_fingerprint(
        subscription_id=subscription_id,
        export_format="json",
        range_start=datetime.fromisoformat("2026-07-01T08:00:00+08:00"),
        range_end=datetime.fromisoformat("2026-07-02T08:00:00+08:00"),
    )
    assert first == second

    with pytest.raises(ExportJobError, match="cannot exceed"):
        validate_export_range(start, end + timedelta(seconds=1), max_range_days=1)
    with pytest.raises(ExportJobError, match="must include"):
        validate_export_range(datetime(2026, 7, 1), end, max_range_days=1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        (" +SUM(A1:A2)", "' +SUM(A1:A2)"),
        ("-2", "'-2"),
        ("@cmd", "'@cmd"),
        ("\tformula", "'\tformula"),
        ("MU", "MU"),
        (120000, 120000),
    ],
)
def test_csv_formula_injection_is_neutralized(value: object, expected: object) -> None:
    assert sanitize_csv_cell(value) == expected


def test_export_paths_require_generated_job_and_lease_nonce(tmp_path) -> None:
    job_id = uuid4()
    nonce = uuid4().hex
    file_name = export_file_name(job_id, "csv", nonce=nonce)
    assert resolve_export_file_path(tmp_path, file_name) == tmp_path.resolve() / file_name
    assert export_file_name(job_id, "csv", nonce=uuid4().hex) != file_name

    with pytest.raises(ValueError, match="invalid export file name"):
        resolve_export_file_path(tmp_path, f"../{file_name}")
    with pytest.raises(ValueError, match="invalid export file name"):
        resolve_export_file_path(tmp_path, f"fare-export-{job_id}.csv")
    with pytest.raises(ValueError, match="nonce"):
        export_file_name(job_id, "csv", nonce="../../unsafe")


def test_open_export_file_survives_concurrent_unlink(tmp_path) -> None:
    job_id = uuid4()
    file_name = export_file_name(job_id, "json", nonce=uuid4().hex)
    contents = b'{"observations":[]}'
    (tmp_path / file_name).write_bytes(contents)

    opened = open_export_file(tmp_path, file_name, expected_size=len(contents))
    assert remove_export_file(tmp_path, file_name) is True
    try:
        assert opened.read() == contents
    finally:
        opened.close()


def test_export_broker_message_uses_database_outbox_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    def capture_send_task(name: str, **kwargs: object) -> None:
        sent.update(name=name, **kwargs)

    monkeypatch.setattr("app.tasks.exports.celery_app.send_task", capture_send_task)
    job_id = uuid4()

    assert enqueue_export_job(job_id) is True
    assert sent == {
        "name": "farescope.exports.run",
        "args": (str(job_id),),
    }


def test_archive_catalog_names_are_strict_and_month_pruned() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    assert archived_partition_overlaps(
        "price_observations_y2026m07",
        range_start=start,
        range_end=end,
    )
    assert not archived_partition_overlaps(
        "price_observations_y2026m06",
        range_start=start,
        range_end=end,
    )
    assert not archived_partition_overlaps(
        "calendar_price_observations_y2026m07",
        range_start=start,
        range_end=end,
    )
    assert not archived_partition_overlaps(
        'price_observations_y2026m07"; DROP TABLE users; --',
        range_start=start,
        range_end=end,
    )

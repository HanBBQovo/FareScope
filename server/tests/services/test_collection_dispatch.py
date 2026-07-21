from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from app.services.collection_dispatch import (
    DispatchLease,
    _publish_with_celery,
    dispatch_token_matches,
    publish_collection_run,
)
from app.tasks.celery_app import celery_app
from app.tasks.collection import collect_collection_run


def test_publish_failure_is_reported_without_raising() -> None:
    lease = DispatchLease(run_id=uuid4(), token="dispatch-token")

    def unavailable_broker(_lease: DispatchLease) -> object:
        raise ConnectionError("broker unavailable")

    result = publish_collection_run(lease, publisher=unavailable_broker)

    assert result.run_id == lease.run_id
    assert result.enqueued is False
    assert result.error_type == "ConnectionError"


def test_dispatch_token_matches_only_the_owning_lease() -> None:
    assert dispatch_token_matches(
        lease_owner="dispatch:token-a",
        dispatch_token="token-a",
    )
    assert not dispatch_token_matches(
        lease_owner="dispatch:token-a",
        dispatch_token="token-b",
    )
    assert not dispatch_token_matches(lease_owner=None, dispatch_token="token-a")


def test_celery_publish_routes_token_to_collector_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = DispatchLease(run_id=uuid4(), token="dispatch-token")
    sent: dict[str, object] = {}

    def capture_send_task(name: str, *args: object, **kwargs: object) -> object:
        sent.update(name=name, args=args, kwargs=kwargs)
        return object()

    monkeypatch.setattr(celery_app, "send_task", capture_send_task)

    _publish_with_celery(lease)

    assert sent["name"] == "farescope.collection.run"
    assert sent["kwargs"] == {
        "args": (str(lease.run_id),),
        "kwargs": {"dispatch_token": lease.token},
        "queue": "collector",
    }


def test_collection_task_accepts_dispatch_token() -> None:
    assert "dispatch_token" in inspect.signature(collect_collection_run.run).parameters

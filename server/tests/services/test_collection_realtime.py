from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services import collection_realtime
from app.services.collection_realtime import (
    COLLECTION_CHECKPOINT_EVENT,
    COLLECTION_SNAPSHOT_EVENT,
    REALTIME_DEGRADED_EVENT,
    CollectionRunEvent,
    collection_run_event_stream,
    encode_sse,
    validate_realtime_cursor,
)
from app.settings import Settings


def _event(*, status: str = "running") -> CollectionRunEvent:
    now = datetime.now(UTC)
    return CollectionRunEvent(
        run_id=uuid4(),
        query_id=uuid4(),
        status=status,
        updated_at=now,
        scheduled_at=now,
        started_at=now,
        finished_at=None,
        attempt=1,
        max_attempts=3,
        error_code=None,
    )


def test_cursor_and_sse_frames_reject_injection_and_keep_safe_fields() -> None:
    assert validate_realtime_cursor("123-4") == "123-4"
    assert validate_realtime_cursor(" 123-4 ") == "123-4"
    assert validate_realtime_cursor(None) is None
    with pytest.raises(ValueError, match="invalid realtime"):
        validate_realtime_cursor("123-4\nevent: leaked")

    event = _event()
    frame = encode_sse("collection-run", event.public_payload(), event_id="123-4")
    assert frame.startswith("id: 123-4\nevent: collection-run\ndata: ")
    assert "queryId" not in frame
    assert "payload" not in frame.casefold()
    assert "cookie" not in frame.casefold()
    assert "secret" not in frame.casefold()


@pytest.mark.asyncio
async def test_redis_outage_still_yields_snapshot_then_observable_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event(status="succeeded")
    client = _UnavailableRedis()

    async def snapshot(*_args: object, **_kwargs: object) -> tuple[CollectionRunEvent, ...]:
        return (event,)

    async def active_session(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(
        collection_realtime, "create_realtime_redis_client", lambda *_a, **_k: client
    )
    monkeypatch.setattr(collection_realtime, "load_initial_collection_snapshot", snapshot)
    monkeypatch.setattr(collection_realtime, "realtime_session_is_active", active_session)
    stream = collection_run_event_stream(
        object(),  # type: ignore[arg-type]
        user_id=uuid4(),
        user_session_id=uuid4(),
        settings=Settings(_env_file=None),
        resume_cursor=None,
    )

    first = await anext(stream)
    second = await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert f"event: {COLLECTION_SNAPSHOT_EVENT}" in first
    assert str(event.run_id) in first
    assert f"event: {REALTIME_DEGRADED_EVENT}" in second
    assert '"reason":"redis_unavailable"' in second
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_close_cancels_and_cleans_up_owned_redis_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _IdleRedis()

    async def snapshot(*_args: object, **_kwargs: object) -> tuple[CollectionRunEvent, ...]:
        return ()

    async def active_session(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(
        collection_realtime, "create_realtime_redis_client", lambda *_a, **_k: client
    )
    monkeypatch.setattr(collection_realtime, "load_initial_collection_snapshot", snapshot)
    monkeypatch.setattr(collection_realtime, "realtime_session_is_active", active_session)
    stream = collection_run_event_stream(
        object(),  # type: ignore[arg-type]
        user_id=uuid4(),
        user_session_id=uuid4(),
        settings=Settings(_env_file=None),
        resume_cursor=None,
    )

    assert f"event: {COLLECTION_SNAPSHOT_EVENT}" in await anext(stream)
    await stream.aclose()

    assert client.closed is True


@pytest.mark.asyncio
async def test_revoked_session_closes_even_when_stream_batches_are_never_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BatchRedis()
    session_checks = iter((True, False))

    async def snapshot(*_args: object, **_kwargs: object) -> tuple[CollectionRunEvent, ...]:
        return ()

    async def active_session(*_args: object, **_kwargs: object) -> bool:
        return next(session_checks)

    monkeypatch.setattr(
        collection_realtime,
        "create_realtime_redis_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(collection_realtime, "load_initial_collection_snapshot", snapshot)
    monkeypatch.setattr(collection_realtime, "realtime_session_is_active", active_session)
    stream = collection_run_event_stream(
        object(),  # type: ignore[arg-type]
        user_id=uuid4(),
        user_session_id=uuid4(),
        settings=Settings(_env_file=None),
        resume_cursor=None,
    )

    assert f"event: {COLLECTION_SNAPSHOT_EVENT}" in await anext(stream)
    expired = await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert f"event: {REALTIME_DEGRADED_EVENT}" in expired
    assert '"reason":"session_expired"' in expired
    assert client.closed is True


@pytest.mark.asyncio
async def test_invisible_records_emit_data_free_checkpoint_after_database_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _BatchRedis()
    checked_run_ids: list[object] = []

    async def snapshot(*_args: object, **_kwargs: object) -> tuple[CollectionRunEvent, ...]:
        return ()

    async def active_session(*_args: object, **_kwargs: object) -> bool:
        return True

    async def invisible_event(*_args: object, **kwargs: object) -> None:
        checked_run_ids.append(kwargs["run_id"])
        return None

    monkeypatch.setattr(
        collection_realtime,
        "create_realtime_redis_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(collection_realtime, "load_initial_collection_snapshot", snapshot)
    monkeypatch.setattr(collection_realtime, "realtime_session_is_active", active_session)
    monkeypatch.setattr(collection_realtime, "load_visible_collection_event", invisible_event)
    stream = collection_run_event_stream(
        object(),  # type: ignore[arg-type]
        user_id=uuid4(),
        user_session_id=uuid4(),
        settings=Settings(_env_file=None),
        resume_cursor="10-0",
    )

    snapshot_frame = await anext(stream)
    checkpoint_frame = await anext(stream)
    await stream.aclose()

    assert snapshot_frame.startswith("id: 10-0")
    assert '"cursor":"10-0"' in snapshot_frame
    assert checkpoint_frame.startswith(f"id: 11-0\nevent: {COLLECTION_CHECKPOINT_EVENT}")
    assert '"cursor":"11-0"' in checkpoint_frame
    assert "run_id" not in checkpoint_frame
    assert "query_id" not in checkpoint_frame
    assert len(checked_run_ids) == 1
    assert client.closed is True


class _IdleRedis:
    closed = False

    async def xrevrange(self, *_args: object, **_kwargs: object) -> list[object]:
        return []

    async def aclose(self) -> None:
        self.closed = True


class _UnavailableRedis(_IdleRedis):
    async def xrevrange(self, *_args: object, **_kwargs: object) -> list[object]:
        raise ConnectionError("test Redis outage")


class _BatchRedis(_IdleRedis):
    async def xread(self, *_args: object, **_kwargs: object) -> list[object]:
        return [
            (
                "farescope:test",
                [
                    (
                        "11-0",
                        {
                            "version": "1",
                            "run_id": str(uuid4()),
                            "query_id": str(uuid4()),
                            "status": "running",
                            "updated_at": datetime.now(UTC).isoformat(),
                        },
                    )
                ],
            )
        ]

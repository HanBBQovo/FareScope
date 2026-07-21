from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.services.collection_realtime import CollectionRunEvent, RedisCollectionEventStore

REDIS_URL = os.getenv("FARESCOPE_TEST_REDIS_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.redis,
    pytest.mark.skipif(
        REDIS_URL is None,
        reason="FARESCOPE_TEST_REDIS_URL is not configured",
    ),
]


async def test_two_clients_publish_and_resume_from_a_stream_cursor() -> None:
    assert REDIS_URL is not None
    stream_key = f"farescope:test:realtime:{uuid4().hex}"
    publisher = Redis.from_url(REDIS_URL, decode_responses=True)
    reader = Redis.from_url(REDIS_URL, decode_responses=True)
    publisher_store = RedisCollectionEventStore(
        publisher,
        stream_key=stream_key,
        max_length=1_000,
    )
    reader_store = RedisCollectionEventStore(
        reader,
        stream_key=stream_key,
        max_length=1_000,
    )
    first = _event("running")
    second = _event("succeeded", run_id=first.run_id, query_id=first.query_id)
    try:
        first_cursor = await publisher_store.publish(first)
        second_cursor = await publisher_store.publish(second)

        resumed = await reader_store.read_after(first_cursor, block_ms=100, count=10)
        raw_rows = await reader.xrange(stream_key)

        assert [record.event_id for record in resumed] == [second_cursor]
        assert [record.run_id for record in resumed] == [first.run_id]
        assert await reader_store.current_cursor() == second_cursor
        assert set(raw_rows[0][1]) <= {
            "version",
            "run_id",
            "query_id",
            "status",
            "updated_at",
            "error_code",
        }
        serialized = repr(raw_rows).casefold()
        assert "payload" not in serialized
        assert "cookie" not in serialized
        assert "secret" not in serialized
    finally:
        await reader.delete(stream_key)
        await publisher.aclose()
        await reader.aclose()


def _event(
    status: str,
    *,
    run_id=None,
    query_id=None,
) -> CollectionRunEvent:
    now = datetime.now(UTC)
    return CollectionRunEvent(
        run_id=run_id or uuid4(),
        query_id=query_id or uuid4(),
        status=status,
        updated_at=now,
        scheduled_at=now,
        started_at=now,
        finished_at=now if status == "succeeded" else None,
        attempt=1,
        max_attempts=3,
        error_code=None,
    )

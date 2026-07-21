from __future__ import annotations

import pytest

from app.api.routes.fares import _run_diagnostics
from app.models import CollectionRun
from app.services import collection_operations
from app.services.collection_operations import extract_top_level_fields, load_queue_depths


class _FakePipeline:
    def __init__(self, values: list[int] | None = None, error: Exception | None = None) -> None:
        self.values = values or []
        self.error = error
        self.queues: list[str] = []

    def llen(self, queue: str) -> _FakePipeline:
        self.queues.append(queue)
        return self

    async def execute(self) -> list[int]:
        if self.error is not None:
            raise self.error
        return self.values


class _FakeRedis:
    def __init__(self, pipeline: _FakePipeline) -> None:
        self.fake_pipeline = pipeline
        self.closed = False

    def pipeline(self, *, transaction: bool) -> _FakePipeline:
        assert transaction is False
        return self.fake_pipeline

    async def aclose(self) -> None:
        self.closed = True


def test_extract_top_level_fields_never_returns_values() -> None:
    assert extract_top_level_fields({"shape": {"data": {"secret": "str"}, "status": "str"}}) == (
        "data",
        "status",
    )
    assert extract_top_level_fields(
        {"shape_truncated": True, "top_level": {"result": "dict", "flag": "bool"}}
    ) == ("flag", "result")
    assert extract_top_level_fields({"shape": "object"}) == ()


def test_run_diagnostics_keep_parse_and_failure_evidence_bounded_and_typed() -> None:
    run = CollectionRun(
        run_metadata={
            "diagnostics": [
                {
                    "code": "field_missing",
                    "path": "data.flight",
                    "message": "Expected flight list",
                    "severity": "warning",
                    "observed_type": "null",
                }
            ],
            "failure": {
                "diagnostics": [
                    {
                        "kind": "anti_bot_432",
                        "message": "Provider blocked the browser response",
                        "retryable": "true",
                    }
                ]
            },
        }
    )

    diagnostics = _run_diagnostics(run)

    assert [item.code for item in diagnostics] == ["field_missing", "anti_bot_432"]
    assert diagnostics[0].severity == "warning"
    assert diagnostics[0].observed_type == "null"
    assert diagnostics[1].severity == "error"
    assert diagnostics[1].retryable is True


@pytest.mark.asyncio
async def test_queue_depths_use_all_celery_ready_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _FakePipeline([3, 1, 2, 4])
    client = _FakeRedis(pipeline)
    monkeypatch.setattr(
        collection_operations.Redis,
        "from_url",
        lambda *_args, **_kwargs: client,
    )

    depths = await load_queue_depths("redis://example.invalid/0")

    assert depths.available is True
    assert (depths.collector, depths.default, depths.analysis, depths.notifications) == (
        3,
        1,
        2,
        4,
    )
    assert pipeline.queues == ["collector", "default", "analysis", "notifications"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_queue_depths_degrade_without_breaking_collection_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis(_FakePipeline(error=TimeoutError("redis unavailable")))
    monkeypatch.setattr(
        collection_operations.Redis,
        "from_url",
        lambda *_args, **_kwargs: client,
    )

    depths = await load_queue_depths("redis://example.invalid/0")

    assert depths.available is False
    assert depths.collector is None
    assert depths.default is None
    assert depths.analysis is None
    assert depths.notifications is None
    assert client.closed is True

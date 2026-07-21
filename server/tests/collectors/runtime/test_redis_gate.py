from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import pytest
from redis.exceptions import RedisError

from app.collectors.runtime import (
    CollectionCoordinationLeaseLostError,
    CollectionCoordinationUnavailableError,
    ProviderRouteGate,
    RateLimitPolicy,
    RedisLeaseCoordinator,
    RedisProviderRouteGate,
)
from app.settings import Settings
from app.tasks.collection import _configured_collection_rate_gate


class _FailingRedis:
    def __init__(self) -> None:
        self.closed = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        del script, numkeys, keys_and_args
        raise RedisError("not available")

    async def aclose(self) -> None:
        self.closed = True


class _ScriptedRedis:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = deque(responses)
        self.closed = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        del script, numkeys, keys_and_args
        return self.responses.popleft()

    async def aclose(self) -> None:
        self.closed = True


class _RenewFailingRedis:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        del script, numkeys, keys_and_args
        self.calls += 1
        if self.calls == 1:
            return [1, 0, 9_999_999_999_999, 9_999_999_999_999]
        if self.calls == 2:
            raise RedisError("renew failed")
        return 1

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_failure_is_fail_closed_without_entering_the_slot() -> None:
    client = _FailingRedis()
    gate = RedisProviderRouteGate(
        "redis://unused",
        RateLimitPolicy(),
        client_factory=lambda: client,
    )
    entered = False

    with pytest.raises(CollectionCoordinationUnavailableError):
        async with gate.slot("ctrip", "SHA-TYO"):
            entered = True

    assert entered is False
    assert client.closed is True


@pytest.mark.asyncio
async def test_lost_owner_release_is_reported_after_the_slot() -> None:
    client = _ScriptedRedis(
        [
            [1, 0, 9_999_999_999_999, 9_999_999_999_999],
            0,
        ]
    )
    gate = RedisProviderRouteGate(
        "redis://unused",
        RateLimitPolicy(minimum_interval_seconds=0, jitter_seconds=0),
        client_factory=lambda: client,
    )

    with pytest.raises(CollectionCoordinationLeaseLostError):
        async with gate.slot("ctrip", "SHA-TYO"):
            pass

    assert client.closed is True


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_the_active_body_and_surfaces_coordination_error() -> None:
    client = _RenewFailingRedis()
    gate = RedisProviderRouteGate(
        "redis://unused",
        RateLimitPolicy(minimum_interval_seconds=0, jitter_seconds=0),
        lease_ttl_seconds=0.15,
        acquire_timeout_seconds=1,
        poll_interval_seconds=0.01,
        client_factory=lambda: client,
    )
    body_completed = False
    started_at = asyncio.get_running_loop().time()

    with pytest.raises(CollectionCoordinationUnavailableError):
        async with gate.slot("ctrip", "SHA-TYO"):
            await asyncio.sleep(1)
            body_completed = True

    assert body_completed is False
    assert asyncio.get_running_loop().time() - started_at < 0.5
    assert client.closed is True


def test_redis_keys_are_bounded_and_share_one_cluster_hash_slot() -> None:
    client = _ScriptedRedis([])
    coordinator = RedisLeaseCoordinator(
        client,
        RateLimitPolicy(),
        lease_ttl_seconds=180,
        key_prefix="farescope:test",
    )

    keys = coordinator.keys_for("CTRIP", "personal-route-value-SHA-TYO")

    assert all("personal-route-value" not in key for key in keys)
    assert len({key[key.index("{") : key.index("}") + 1] for key in keys}) == 1
    assert all(len(key) < 180 for key in keys)


def test_local_fallback_is_explicit_and_redis_is_selected_for_production_config() -> None:
    local_gate = _configured_collection_rate_gate(
        Settings(collection_coordination_backend="local")
    )
    redis_gate = _configured_collection_rate_gate(
        Settings(collection_coordination_backend="redis")
    )

    assert isinstance(local_gate, ProviderRouteGate)
    assert isinstance(redis_gate, RedisProviderRouteGate)

    with pytest.raises(ValueError, match="required in production"):
        Settings(
            environment="production",
            session_cookie_secure=True,
            secret_encryption_key="test-key",
            collection_coordination_backend="local",
        )
    with pytest.raises(ValueError):
        Settings(collection_coordination_lease_ttl_seconds=149)
    with pytest.raises(ValueError, match="shorter than the run lease"):
        Settings(
            collection_coordination_backend="redis",
            collection_coordination_acquire_timeout_seconds=900,
            collection_run_lease_seconds=900,
        )

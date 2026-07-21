from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.collectors.runtime import (
    RateLimitPolicy,
    RedisLeaseCoordinator,
    RedisProviderRouteGate,
)

REDIS_URL = os.getenv("FARESCOPE_TEST_REDIS_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.redis,
    pytest.mark.skipif(
        REDIS_URL is None,
        reason="FARESCOPE_TEST_REDIS_URL is not configured",
    ),
]


@pytest.mark.parametrize(
    ("provider_limit", "first_route", "second_route"),
    (
        (1, "SHA-TYO", "SHA-KIX"),
        (2, "SHA-TYO", "SHA-TYO"),
    ),
)
async def test_multi_instance_provider_and_route_competition_is_atomic(
    provider_limit: int,
    first_route: str,
    second_route: str,
) -> None:
    assert REDIS_URL is not None
    prefix = f"farescope:test:{uuid4().hex}"
    clients, coordinators = _coordinators(
        REDIS_URL,
        prefix,
        RateLimitPolicy(
            provider_concurrency=provider_limit,
            route_concurrency=1,
            minimum_interval_seconds=0,
            jitter_seconds=0,
        ),
        lease_ttl_seconds=1,
    )
    try:
        attempts = await asyncio.gather(
            coordinators[0].try_acquire(
                "ctrip",
                first_route,
                token="owner-one",
                jitter_seconds=0,
            ),
            coordinators[1].try_acquire(
                "ctrip",
                second_route,
                token="owner-two",
                jitter_seconds=0,
            ),
        )

        assert sum(attempt.acquired for attempt in attempts) == 1
        winner_index = 0 if attempts[0].acquired else 1
        loser_index = 1 - winner_index
        winner_lease = attempts[winner_index].lease
        assert winner_lease is not None
        assert await coordinators[winner_index].release(winner_lease) is True

        retry = await coordinators[loser_index].try_acquire(
            "ctrip",
            second_route if loser_index == 1 else first_route,
            token="owner-two" if loser_index == 1 else "owner-one",
            jitter_seconds=0,
        )
        assert retry.acquired is True
        assert retry.lease is not None
        assert await coordinators[loser_index].release(retry.lease) is True
    finally:
        await _close_and_clean(REDIS_URL, prefix, clients)


async def test_expired_token_is_recovered_without_an_explicit_release() -> None:
    assert REDIS_URL is not None
    prefix = f"farescope:test:{uuid4().hex}"
    clients, coordinators = _coordinators(
        REDIS_URL,
        prefix,
        RateLimitPolicy(
            provider_concurrency=1,
            route_concurrency=1,
            minimum_interval_seconds=0,
            jitter_seconds=0,
        ),
        lease_ttl_seconds=0.15,
    )
    try:
        first = await coordinators[0].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="crashed-owner",
            jitter_seconds=0,
        )
        assert first.acquired is True

        await asyncio.sleep(0.22)
        recovered = await coordinators[1].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="replacement-owner",
            jitter_seconds=0,
        )
        assert recovered.acquired is True
        assert recovered.lease is not None
        assert await coordinators[1].release(recovered.lease) is True
    finally:
        await _close_and_clean(REDIS_URL, prefix, clients)


async def test_active_gate_renews_its_token_before_ttl_expiry() -> None:
    assert REDIS_URL is not None
    prefix = f"farescope:test:{uuid4().hex}"
    policy = RateLimitPolicy(
        provider_concurrency=1,
        route_concurrency=1,
        minimum_interval_seconds=0,
        jitter_seconds=0,
    )
    contender_client = Redis.from_url(REDIS_URL)
    contender = RedisLeaseCoordinator(
        contender_client,
        policy,
        lease_ttl_seconds=0.15,
        key_prefix=prefix,
    )
    gate = RedisProviderRouteGate(
        REDIS_URL,
        policy,
        lease_ttl_seconds=0.15,
        acquire_timeout_seconds=0.5,
        poll_interval_seconds=0.02,
        command_timeout_seconds=0.5,
        key_prefix=prefix,
    )
    try:
        async with gate.slot("ctrip", "SHA-TYO"):
            await asyncio.sleep(0.24)
            blocked = await contender.try_acquire(
                "ctrip",
                "SHA-TYO",
                token="contender",
                jitter_seconds=0,
            )
            assert blocked.acquired is False

        allowed = await contender.try_acquire(
            "ctrip",
            "SHA-TYO",
            token="contender",
            jitter_seconds=0,
        )
        assert allowed.acquired is True
        assert allowed.lease is not None
        assert await contender.release(allowed.lease) is True
    finally:
        await _close_and_clean(REDIS_URL, prefix, [contender_client])


async def test_wrong_owner_cannot_release_an_active_token() -> None:
    assert REDIS_URL is not None
    prefix = f"farescope:test:{uuid4().hex}"
    clients, coordinators = _coordinators(
        REDIS_URL,
        prefix,
        RateLimitPolicy(
            provider_concurrency=1,
            route_concurrency=1,
            minimum_interval_seconds=0,
            jitter_seconds=0,
        ),
        lease_ttl_seconds=1,
    )
    try:
        first = await coordinators[0].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="real-owner",
            jitter_seconds=0,
        )
        assert first.lease is not None
        forged = replace(first.lease, token="wrong-owner")

        assert await coordinators[1].release(forged) is False
        blocked = await coordinators[1].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="waiting-owner",
            jitter_seconds=0,
        )
        assert blocked.acquired is False

        assert await coordinators[0].release(first.lease) is True
        allowed = await coordinators[1].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="waiting-owner",
            jitter_seconds=0,
        )
        assert allowed.acquired is True
        assert allowed.lease is not None
        assert await coordinators[1].release(allowed.lease) is True
    finally:
        await _close_and_clean(REDIS_URL, prefix, clients)


async def test_minimum_start_interval_and_jitter_are_shared_between_instances() -> None:
    assert REDIS_URL is not None
    prefix = f"farescope:test:{uuid4().hex}"
    clients, coordinators = _coordinators(
        REDIS_URL,
        prefix,
        RateLimitPolicy(
            provider_concurrency=2,
            route_concurrency=2,
            minimum_interval_seconds=0.1,
            jitter_seconds=0.1,
        ),
        lease_ttl_seconds=1,
    )
    try:
        first = await coordinators[0].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="first-owner",
            jitter_seconds=0.1,
        )
        assert first.lease is not None
        assert await coordinators[0].release(first.lease) is True

        blocked = await coordinators[1].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="second-owner",
            jitter_seconds=0,
        )
        assert blocked.acquired is False
        assert 0.05 <= blocked.retry_after_seconds <= 0.25

        await asyncio.sleep(blocked.retry_after_seconds + 0.03)
        allowed = await coordinators[1].try_acquire(
            "ctrip",
            "SHA-TYO",
            token="second-owner",
            jitter_seconds=0,
        )
        assert allowed.acquired is True
        assert allowed.lease is not None
        assert await coordinators[1].release(allowed.lease) is True
    finally:
        await _close_and_clean(REDIS_URL, prefix, clients)


def _coordinators(
    redis_url: str,
    prefix: str,
    policy: RateLimitPolicy,
    *,
    lease_ttl_seconds: float,
) -> tuple[list[Redis], list[RedisLeaseCoordinator]]:
    clients = [Redis.from_url(redis_url), Redis.from_url(redis_url)]
    coordinators = [
        RedisLeaseCoordinator(
            client,
            policy,
            lease_ttl_seconds=lease_ttl_seconds,
            key_prefix=prefix,
        )
        for client in clients
    ]
    return clients, coordinators


async def _close_and_clean(redis_url: str, prefix: str, clients: list[Redis]) -> None:
    cleanup = Redis.from_url(redis_url)
    try:
        keys = [key async for key in cleanup.scan_iter(match=f"{prefix}:*")]
        if keys:
            await cleanup.delete(*keys)
    finally:
        await cleanup.aclose()
        for client in clients:
            await client.aclose()

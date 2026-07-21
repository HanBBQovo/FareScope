from __future__ import annotations

import asyncio

import pytest

from app.collectors.runtime.policy import (
    CollectionExecutor,
    ProviderRouteGate,
    RateLimitPolicy,
    RetryPolicy,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


@pytest.mark.asyncio
async def test_route_starts_are_separated_by_interval_and_jitter() -> None:
    fake_time = FakeTime()
    gate = ProviderRouteGate(
        RateLimitPolicy(minimum_interval_seconds=2, jitter_seconds=1),
        clock=fake_time.clock,
        sleep=fake_time.sleep,
        random_fraction=lambda: 0.5,
    )

    async with gate.slot("ctrip", "SHA-TYO"):
        pass
    async with gate.slot("ctrip", "SHA-TYO"):
        pass

    assert fake_time.sleeps == [2.5]


@pytest.mark.asyncio
async def test_route_concurrency_is_bounded() -> None:
    gate = ProviderRouteGate(
        RateLimitPolicy(
            provider_concurrency=2,
            route_concurrency=1,
            minimum_interval_seconds=0,
            jitter_seconds=0,
        )
    )
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with gate.slot("ctrip", "SHA-TYO"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        async with gate.slot("ctrip", "SHA-TYO"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)

    assert second_entered.is_set() is False
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set() is True


@pytest.mark.asyncio
async def test_waiting_same_route_does_not_consume_provider_capacity() -> None:
    gate = ProviderRouteGate(
        RateLimitPolicy(
            provider_concurrency=2,
            route_concurrency=1,
            minimum_interval_seconds=0,
            jitter_seconds=0,
        )
    )
    release_first = asyncio.Event()
    first_entered = asyncio.Event()
    other_route_entered = asyncio.Event()

    async def hold_first_route() -> None:
        async with gate.slot("ctrip", "SHA-TYO"):
            first_entered.set()
            await release_first.wait()

    async def wait_first_route() -> None:
        async with gate.slot("ctrip", "SHA-TYO"):
            pass

    async def use_other_route() -> None:
        async with gate.slot("ctrip", "SHA-KIX"):
            other_route_entered.set()

    first_task = asyncio.create_task(hold_first_route())
    await first_entered.wait()
    waiting_task = asyncio.create_task(wait_first_route())
    other_task = asyncio.create_task(use_other_route())
    await asyncio.wait_for(other_route_entered.wait(), timeout=0.1)

    release_first.set()
    await asyncio.gather(first_task, waiting_task, other_task)


@pytest.mark.asyncio
async def test_executor_retries_with_bounded_exponential_backoff() -> None:
    fake_time = FakeTime()
    gate = ProviderRouteGate(
        RateLimitPolicy(minimum_interval_seconds=0, jitter_seconds=0),
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )
    executor = CollectionExecutor(
        gate,
        RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1,
            multiplier=2,
            maximum_delay_seconds=10,
            jitter_ratio=0.2,
        ),
        sleep=fake_time.sleep,
        random_fraction=lambda: 0.5,
    )
    attempts: list[int] = []

    async def operation(attempt: int) -> str:
        attempts.append(attempt)
        if attempt < 3:
            raise TimeoutError
        return "ok"

    result = await executor.execute(
        provider="ctrip",
        route_key="SHA-TYO",
        operation=operation,
    )

    assert result == "ok"
    assert attempts == [1, 2, 3]
    assert fake_time.sleeps == [1.1, 2.2]


@pytest.mark.asyncio
async def test_executor_can_retry_a_classified_result() -> None:
    fake_time = FakeTime()
    executor = CollectionExecutor(
        ProviderRouteGate(
            RateLimitPolicy(minimum_interval_seconds=0, jitter_seconds=0),
            clock=fake_time.clock,
            sleep=fake_time.sleep,
        ),
        RetryPolicy(max_attempts=2, initial_delay_seconds=0, maximum_delay_seconds=0),
        sleep=fake_time.sleep,
    )
    outcomes = iter(["retry", "done"])

    result = await executor.execute(
        provider="ctrip",
        route_key="SHA-TYO",
        operation=lambda _attempt: _async_value(next(outcomes)),
        retry_result=lambda value: value == "retry",
    )

    assert result == "done"


async def _async_value(value: str) -> str:
    return value


def test_retry_delay_is_capped() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=10,
        multiplier=3,
        maximum_delay_seconds=20,
        jitter_ratio=1,
    )

    assert policy.delay_after(4, random_fraction=1) == 20

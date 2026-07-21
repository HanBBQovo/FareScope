"""Bounded provider/route scheduling and retry policies."""

from __future__ import annotations

import asyncio
import inspect
import random
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

ResultT = TypeVar("ResultT")


class CollectionRateGate(Protocol):
    """Common interface for local and distributed collection coordination."""

    def slot(
        self,
        provider: str,
        route_key: str,
    ) -> AbstractAsyncContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    provider_concurrency: int = 2
    route_concurrency: int = 1
    minimum_interval_seconds: float = 3.0
    jitter_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.provider_concurrency < 1 or self.route_concurrency < 1:
            raise ValueError("Concurrency limits must be positive")
        if self.minimum_interval_seconds < 0 or self.jitter_seconds < 0:
            raise ValueError("Intervals must not be negative")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 2.0
    multiplier: float = 2.0
    maximum_delay_seconds: float = 60.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("Retry delays must not be negative")
        if self.multiplier < 1:
            raise ValueError("Retry multiplier must be at least one")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")

    def delay_after(self, failed_attempt: int, *, random_fraction: float) -> float:
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be positive")
        bounded_fraction = min(1.0, max(0.0, random_fraction))
        base = min(
            self.maximum_delay_seconds,
            self.initial_delay_seconds * self.multiplier ** (failed_attempt - 1),
        )
        return min(
            self.maximum_delay_seconds,
            base + base * self.jitter_ratio * bounded_fraction,
        )


class ProviderRouteGate:
    """Coordinate concurrency and paced starts per provider/route pair."""

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fraction: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._sleep = sleep
        self._random_fraction = random_fraction
        # Celery's synchronous task wrapper may create a fresh asyncio loop for each
        # task. Threading primitives keep the process-local gate effective across
        # those loops while the async API remains convenient for browser I/O.
        self._provider_semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._route_semaphores: dict[tuple[str, str], threading.BoundedSemaphore] = {}
        self._next_start: dict[tuple[str, str], float] = {}
        self._schedule_lock = threading.Lock()

    @asynccontextmanager
    async def slot(self, provider: str, route_key: str):
        provider_key = provider.casefold()
        route = (provider_key, route_key.casefold())
        provider_semaphore = self._provider_semaphores.setdefault(
            provider_key,
            threading.BoundedSemaphore(self.policy.provider_concurrency),
        )
        route_semaphore = self._route_semaphores.setdefault(
            route,
            threading.BoundedSemaphore(self.policy.route_concurrency),
        )

        await asyncio.to_thread(route_semaphore.acquire)
        try:
            await asyncio.to_thread(provider_semaphore.acquire)
            try:
                delay = await self._reserve_start(route)
                if delay > 0:
                    await self._sleep(delay)
                yield
            finally:
                provider_semaphore.release()
        finally:
            route_semaphore.release()

    async def _reserve_start(self, route: tuple[str, str]) -> float:
        with self._schedule_lock:
            now = self._clock()
            scheduled_start = max(now, self._next_start.get(route, now))
            jitter = self.policy.jitter_seconds * min(
                1.0,
                max(0.0, self._random_fraction()),
            )
            self._next_start[route] = (
                scheduled_start + self.policy.minimum_interval_seconds + jitter
            )
            return scheduled_start - now


class CollectionExecutor:
    """Run collection operations through one gate and bounded retry loop."""

    def __init__(
        self,
        gate: CollectionRateGate,
        retry_policy: RetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fraction: Callable[[], float] = random.random,
    ) -> None:
        self.gate = gate
        self.retry_policy = retry_policy
        self._sleep = sleep
        self._random_fraction = random_fraction

    async def execute(
        self,
        *,
        provider: str,
        route_key: str,
        operation: Callable[[int], Awaitable[ResultT]],
        retry_result: Callable[[ResultT], bool] | None = None,
        retry_exception: Callable[[Exception], bool] | None = None,
        on_retry: Callable[[int, float, Any], object | Awaitable[object]] | None = None,
    ) -> ResultT:
        retry_result = retry_result or (lambda _result: False)
        retry_exception = retry_exception or _default_retry_exception

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                async with self.gate.slot(provider, route_key):
                    result = await operation(attempt)
            except Exception as exc:
                if attempt >= self.retry_policy.max_attempts or not retry_exception(exc):
                    raise
                await self._pause_before_retry(attempt, exc, on_retry)
                continue

            if attempt >= self.retry_policy.max_attempts or not retry_result(result):
                return result
            await self._pause_before_retry(attempt, result, on_retry)

        raise RuntimeError("Retry loop exhausted without a result")

    async def _pause_before_retry(
        self,
        failed_attempt: int,
        outcome: Any,
        callback: Callable[[int, float, Any], object | Awaitable[object]] | None,
    ) -> None:
        delay = self.retry_policy.delay_after(
            failed_attempt,
            random_fraction=self._random_fraction(),
        )
        if callback is not None:
            callback_result = callback(failed_attempt, delay, outcome)
            if inspect.isawaitable(callback_result):
                await callback_result
        if delay > 0:
            await self._sleep(delay)


def _default_retry_exception(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError))

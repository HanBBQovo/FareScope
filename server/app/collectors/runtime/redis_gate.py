"""Redis-backed provider and route coordination for collector workers."""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.collectors.runtime.policy import RateLimitPolicy

_ACQUIRE_SCRIPT = """
local provider_key = KEYS[1]
local route_key = KEYS[2]
local pace_key = KEYS[3]
local token = ARGV[1]
local provider_limit = tonumber(ARGV[2])
local route_limit = tonumber(ARGV[3])
local lease_ttl_ms = tonumber(ARGV[4])
local interval_ms = tonumber(ARGV[5])
local jitter_ms = tonumber(ARGV[6])
local pace_ttl_ms = tonumber(ARGV[7])

local redis_time = redis.call('TIME')
local now_ms = redis_time[1] * 1000 + math.floor(redis_time[2] / 1000)

redis.call('ZREMRANGEBYSCORE', provider_key, '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', route_key, '-inf', now_ms)

local provider_score = tonumber(redis.call('ZSCORE', provider_key, token))
local route_score = tonumber(redis.call('ZSCORE', route_key, token))
if provider_score and route_score and provider_score > now_ms and route_score > now_ms then
    local expires_at = math.min(provider_score, route_score)
    local next_start_at = tonumber(redis.call('GET', pace_key)) or now_ms
    return {1, 0, expires_at, next_start_at}
end
if provider_score or route_score then
    redis.call('ZREM', provider_key, token)
    redis.call('ZREM', route_key, token)
end

local provider_count = redis.call('ZCARD', provider_key)
local route_count = redis.call('ZCARD', route_key)
local next_start_at = tonumber(redis.call('GET', pace_key)) or 0
local wait_ms = 0

if provider_count >= provider_limit then
    local first = redis.call('ZRANGE', provider_key, 0, 0, 'WITHSCORES')
    if first[2] then
        wait_ms = math.max(wait_ms, tonumber(first[2]) - now_ms)
    end
end
if route_count >= route_limit then
    local first = redis.call('ZRANGE', route_key, 0, 0, 'WITHSCORES')
    if first[2] then
        wait_ms = math.max(wait_ms, tonumber(first[2]) - now_ms)
    end
end
if next_start_at > now_ms then
    wait_ms = math.max(wait_ms, next_start_at - now_ms)
end

if provider_count >= provider_limit or route_count >= route_limit or next_start_at > now_ms then
    return {0, math.max(wait_ms, 1), 0, next_start_at}
end

local expires_at = now_ms + lease_ttl_ms
next_start_at = now_ms + interval_ms + jitter_ms
redis.call('ZADD', provider_key, expires_at, token)
redis.call('ZADD', route_key, expires_at, token)
redis.call('PEXPIRE', provider_key, lease_ttl_ms + 1000)
redis.call('PEXPIRE', route_key, lease_ttl_ms + 1000)
redis.call('SET', pace_key, next_start_at, 'PX', pace_ttl_ms)
return {1, 0, expires_at, next_start_at}
"""

_RENEW_SCRIPT = """
local provider_key = KEYS[1]
local route_key = KEYS[2]
local token = ARGV[1]
local lease_ttl_ms = tonumber(ARGV[2])

local redis_time = redis.call('TIME')
local now_ms = redis_time[1] * 1000 + math.floor(redis_time[2] / 1000)
local provider_score = tonumber(redis.call('ZSCORE', provider_key, token))
local route_score = tonumber(redis.call('ZSCORE', route_key, token))

if not provider_score or not route_score or provider_score <= now_ms or route_score <= now_ms then
    redis.call('ZREM', provider_key, token)
    redis.call('ZREM', route_key, token)
    return {0, now_ms}
end

local expires_at = now_ms + lease_ttl_ms
redis.call('ZADD', provider_key, expires_at, token)
redis.call('ZADD', route_key, expires_at, token)
redis.call('PEXPIRE', provider_key, lease_ttl_ms + 1000)
redis.call('PEXPIRE', route_key, lease_ttl_ms + 1000)
return {1, expires_at}
"""

_RELEASE_SCRIPT = """
local provider_key = KEYS[1]
local route_key = KEYS[2]
local token = ARGV[1]
local provider_removed = redis.call('ZREM', provider_key, token)
local route_removed = redis.call('ZREM', route_key, token)

if redis.call('ZCARD', provider_key) == 0 then
    redis.call('DEL', provider_key)
end
if redis.call('ZCARD', route_key) == 0 then
    redis.call('DEL', route_key)
end

if provider_removed == 1 and route_removed == 1 then
    return 1
end
return 0
"""


class RedisEvalClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...

    async def aclose(self) -> None: ...


class CollectionCoordinationError(RuntimeError):
    """Base class for failures that must stop provider traffic."""

    code = "collection_coordination_error"


class CollectionCoordinationUnavailableError(CollectionCoordinationError):
    code = "collection_coordination_unavailable"


class CollectionCoordinationTimeoutError(CollectionCoordinationError):
    code = "collection_coordination_timeout"


class CollectionCoordinationLeaseLostError(CollectionCoordinationError):
    code = "collection_coordination_lease_lost"


@dataclass(frozen=True, slots=True)
class RedisCoordinationLease:
    provider_key: str
    route_key: str
    pace_key: str
    token: str
    expires_at_ms: int
    next_start_at_ms: int


@dataclass(frozen=True, slots=True)
class RedisAcquireAttempt:
    acquired: bool
    retry_after_seconds: float
    lease: RedisCoordinationLease | None = None


class RedisLeaseCoordinator:
    """Thin, testable boundary around the atomic Redis lease scripts."""

    def __init__(
        self,
        client: RedisEvalClient,
        policy: RateLimitPolicy,
        *,
        lease_ttl_seconds: float,
        key_prefix: str = "farescope:collection-coordination",
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("coordination lease TTL must be positive")
        normalized_prefix = key_prefix.strip().strip(":")
        if (
            not normalized_prefix
            or len(normalized_prefix) > 80
            or any(character.isspace() or character in "{}" for character in normalized_prefix)
        ):
            raise ValueError("coordination key prefix must contain 1 to 80 characters")
        self.client = client
        self.policy = policy
        self.lease_ttl_ms = max(1, round(lease_ttl_seconds * 1000))
        namespace_hash = hashlib.sha256(normalized_prefix.encode("utf-8")).hexdigest()[:16]
        self.key_root = f"{normalized_prefix}:{{{namespace_hash}}}"

    async def try_acquire(
        self,
        provider: str,
        route_key: str,
        *,
        token: str,
        jitter_seconds: float,
    ) -> RedisAcquireAttempt:
        if not token or len(token) > 128:
            raise ValueError("coordination owner token must contain 1 to 128 characters")
        provider_key, redis_route_key, pace_key = self.keys_for(provider, route_key)
        interval_ms = max(0, round(self.policy.minimum_interval_seconds * 1000))
        bounded_jitter = min(self.policy.jitter_seconds, max(0, jitter_seconds))
        jitter_ms = round(bounded_jitter * 1000)
        pace_ttl_ms = max(1000, interval_ms + jitter_ms + 1000)
        response = await self._eval(
            _ACQUIRE_SCRIPT,
            3,
            provider_key,
            redis_route_key,
            pace_key,
            token,
            self.policy.provider_concurrency,
            self.policy.route_concurrency,
            self.lease_ttl_ms,
            interval_ms,
            jitter_ms,
            pace_ttl_ms,
        )
        values = _integer_response(response, minimum_length=4)
        if values[0] == 1:
            return RedisAcquireAttempt(
                acquired=True,
                retry_after_seconds=0,
                lease=RedisCoordinationLease(
                    provider_key=provider_key,
                    route_key=redis_route_key,
                    pace_key=pace_key,
                    token=token,
                    expires_at_ms=values[2],
                    next_start_at_ms=values[3],
                ),
            )
        return RedisAcquireAttempt(
            acquired=False,
            retry_after_seconds=max(0.001, values[1] / 1000),
        )

    async def renew(self, lease: RedisCoordinationLease) -> bool:
        response = await self._eval(
            _RENEW_SCRIPT,
            2,
            lease.provider_key,
            lease.route_key,
            lease.token,
            self.lease_ttl_ms,
        )
        return _integer_response(response, minimum_length=2)[0] == 1

    async def release(self, lease: RedisCoordinationLease) -> bool:
        response = await self._eval(
            _RELEASE_SCRIPT,
            2,
            lease.provider_key,
            lease.route_key,
            lease.token,
        )
        try:
            return int(response) == 1
        except (TypeError, ValueError) as exc:
            raise CollectionCoordinationUnavailableError(
                "Redis returned an invalid coordination response"
            ) from exc

    def keys_for(self, provider: str, route_key: str) -> tuple[str, str, str]:
        provider_hash = _key_hash(provider)
        route_hash = _key_hash(f"{provider.casefold()}:{route_key.casefold()}")
        return (
            f"{self.key_root}:provider:{provider_hash}",
            f"{self.key_root}:route:{provider_hash}:{route_hash}",
            f"{self.key_root}:pace:{provider_hash}:{route_hash}",
        )

    async def _eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        try:
            return await self.client.eval(script, numkeys, *keys_and_args)
        except (RedisError, OSError, TimeoutError) as exc:
            raise CollectionCoordinationUnavailableError(
                "Redis collection coordination is unavailable"
            ) from exc


class RedisProviderRouteGate:
    """Coordinate collector starts across processes and hosts through Redis."""

    def __init__(
        self,
        redis_url: str,
        policy: RateLimitPolicy,
        *,
        lease_ttl_seconds: float = 180,
        acquire_timeout_seconds: float = 120,
        poll_interval_seconds: float = 0.5,
        command_timeout_seconds: float = 2,
        key_prefix: str = "farescope:collection-coordination",
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fraction: Callable[[], float] = random.random,
        token_factory: Callable[[], str] = lambda: uuid4().hex,
        client_factory: Callable[[], RedisEvalClient] | None = None,
    ) -> None:
        if acquire_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("coordination acquire timeout and poll interval must be positive")
        if command_timeout_seconds <= 0:
            raise ValueError("Redis command timeout must be positive")
        self.redis_url = redis_url
        self.policy = policy
        self.lease_ttl_seconds = lease_ttl_seconds
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.command_timeout_seconds = command_timeout_seconds
        self.key_prefix = key_prefix
        self._sleep = sleep
        self._random_fraction = random_fraction
        self._token_factory = token_factory
        self._client_factory = client_factory or self._new_client

    @asynccontextmanager
    async def slot(self, provider: str, route_key: str):
        client = self._client_factory()
        coordinator = RedisLeaseCoordinator(
            client,
            self.policy,
            lease_ttl_seconds=self.lease_ttl_seconds,
            key_prefix=self.key_prefix,
        )
        try:
            lease = await self._wait_for_lease(coordinator, provider, route_key)
            stop_heartbeat = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._renew_lease(coordinator, lease, stop_heartbeat)
            )
            owner_task = asyncio.current_task()
            if owner_task is None:
                raise RuntimeError("Redis collection slot requires an asyncio task")

            def stop_body_on_heartbeat_failure(task: asyncio.Task[None]) -> None:
                if _coordination_task_error(task) is not None and not owner_task.done():
                    owner_task.cancel()

            heartbeat.add_done_callback(stop_body_on_heartbeat_failure)
            body_failed = False
            try:
                yield
            except asyncio.CancelledError:
                body_failed = True
                heartbeat_error = _coordination_task_error(heartbeat)
                if heartbeat_error is not None:
                    raise heartbeat_error from None
                raise
            except BaseException:
                body_failed = True
                raise
            finally:
                stop_heartbeat.set()
                coordination_error: CollectionCoordinationError | None = None
                try:
                    await heartbeat
                except CollectionCoordinationError as exc:
                    coordination_error = exc
                try:
                    released = await coordinator.release(lease)
                    if not released and coordination_error is None:
                        coordination_error = CollectionCoordinationLeaseLostError(
                            "Redis collection coordination lease was no longer owned"
                        )
                except CollectionCoordinationError as exc:
                    if coordination_error is None:
                        coordination_error = exc
                if not body_failed and coordination_error is not None:
                    raise coordination_error
        finally:
            with suppress(Exception):  # lease TTL remains the crash-safe fallback
                await client.aclose()

    async def _wait_for_lease(
        self,
        coordinator: RedisLeaseCoordinator,
        provider: str,
        route_key: str,
    ) -> RedisCoordinationLease:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.acquire_timeout_seconds
        token = self._token_factory()
        jitter_fraction = min(1.0, max(0.0, self._random_fraction()))
        jitter_seconds = self.policy.jitter_seconds * jitter_fraction

        while True:
            attempt = await coordinator.try_acquire(
                provider,
                route_key,
                token=token,
                jitter_seconds=jitter_seconds,
            )
            if attempt.acquired and attempt.lease is not None:
                return attempt.lease
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise CollectionCoordinationTimeoutError(
                    "Timed out waiting for a distributed collection slot"
                )
            await self._sleep(
                min(self.poll_interval_seconds, attempt.retry_after_seconds, remaining)
            )

    async def _renew_lease(
        self,
        coordinator: RedisLeaseCoordinator,
        lease: RedisCoordinationLease,
        stop: asyncio.Event,
    ) -> None:
        renew_interval = max(0.05, self.lease_ttl_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=renew_interval)
                return
            except TimeoutError:
                pass
            if not await coordinator.renew(lease):
                raise CollectionCoordinationLeaseLostError(
                    "Redis collection coordination lease expired before renewal"
                )

    def _new_client(self) -> RedisEvalClient:
        return Redis.from_url(
            self.redis_url,
            socket_connect_timeout=self.command_timeout_seconds,
            socket_timeout=self.command_timeout_seconds,
            health_check_interval=30,
            retry_on_timeout=False,
        )


def _integer_response(response: Any, *, minimum_length: int) -> tuple[int, ...]:
    if not isinstance(response, (list, tuple)) or len(response) < minimum_length:
        raise CollectionCoordinationUnavailableError(
            "Redis returned an invalid coordination response"
        )
    try:
        return tuple(int(value) for value in response)
    except (TypeError, ValueError) as exc:
        raise CollectionCoordinationUnavailableError(
            "Redis returned an invalid coordination response"
        ) from exc


def _key_hash(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("provider and route key must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _coordination_task_error(
    task: asyncio.Task[None],
) -> CollectionCoordinationError | None:
    if not task.done() or task.cancelled():
        return None
    exception = task.exception()
    return exception if isinstance(exception, CollectionCoordinationError) else None

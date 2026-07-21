"""Celery entry point for one headed-browser collection run."""

from __future__ import annotations

import asyncio
import math
import os
import random
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.runtime import (
    BrowserRunConfig,
    BrowserRunResult,
    CaptureDiagnostic,
    FailureKind,
    PlaywrightCaptureRunner,
    ProviderRouteGate,
    RateLimitPolicy,
    RetryPolicy,
    ctrip_capture_rules,
)
from app.db.session import create_engine, create_session_factory
from app.models import CollectionRun, Provider, SearchLeg, SearchQuery
from app.models.enums import CollectionStatus
from app.services.collection_dispatch import dispatch_token_matches
from app.services.collection_persistence import (
    finalize_collection_success,
    persist_collection_payloads,
    record_collection_failure,
)
from app.settings import Settings, get_settings
from app.tasks.celery_app import celery_app

_EXPECTED_CAPTURES = frozenset({"calendar", "batch_search"})


@lru_cache(maxsize=32)
def _collection_rate_gate(
    provider_concurrency: int,
    route_concurrency: int,
    minimum_interval_seconds: float,
    jitter_seconds: float,
) -> ProviderRouteGate:
    """Keep pacing state across Celery task event loops within one worker process."""

    return ProviderRouteGate(
        RateLimitPolicy(
            provider_concurrency=provider_concurrency,
            route_concurrency=route_concurrency,
            minimum_interval_seconds=minimum_interval_seconds,
            jitter_seconds=jitter_seconds,
        )
    )


@dataclass(frozen=True, slots=True)
class CollectionClaim:
    run_id: UUID
    search_query_id: UUID
    provider_id: UUID
    provider_code: str
    route_key: str
    page_url: str


class CollectionRunNotFoundError(LookupError):
    pass


class CollectionRunUnavailableError(RuntimeError):
    pass


async def run_collection_once(
    collection_run_id: UUID | str,
    *,
    proxy_server: str | None = None,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    runner: PlaywrightCaptureRunner | None = None,
    rate_gate: ProviderRouteGate | None = None,
    worker_id: str | None = None,
    dispatch_token: str | None = None,
) -> dict[str, Any]:
    """Claim, collect, and persist one run without holding a DB transaction during I/O."""

    run_id = UUID(str(collection_run_id))
    runtime_settings = settings or get_settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_engine(
            runtime_settings.database_url,
            echo=runtime_settings.database_echo,
            pool_size=runtime_settings.database_pool_size,
            max_overflow=runtime_settings.database_max_overflow,
            pool_timeout_seconds=runtime_settings.database_pool_timeout_seconds,
            pool_recycle_seconds=runtime_settings.database_pool_recycle_seconds,
            statement_timeout_ms=runtime_settings.database_statement_timeout_ms,
            application_name="farescope-collector",
        )
        session_factory = create_session_factory(owned_engine)
    capture_runner = runner or PlaywrightCaptureRunner()
    collection_rate_gate = rate_gate or _collection_rate_gate(
        runtime_settings.collection_provider_concurrency,
        runtime_settings.collection_route_concurrency,
        runtime_settings.collection_minimum_interval_seconds,
        runtime_settings.collection_jitter_seconds,
    )
    lease_owner = (worker_id or os.getenv("FARESCOPE_WORKER_ID") or socket.gethostname())[:160]
    worker_lease_owner = f"worker:{lease_owner}"[:160]

    try:
        retry_at: datetime | None = None
        try:
            async with session_factory() as session, session.begin():
                claim = await _claim_collection_run(
                    session,
                    run_id=run_id,
                    worker_id=lease_owner,
                    dispatch_token=dispatch_token,
                    lease_seconds=runtime_settings.collection_run_lease_seconds,
                    browser_channel=runtime_settings.collector_browser_channel,
                )
        except CollectionRunUnavailableError as exc:
            return {
                "run_id": str(run_id),
                "status": "skipped",
                "reason": str(exc),
            }
        except CollectionRunNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - claim/config failure needs durable state
            await _record_unhandled_failure(
                session_factory,
                run_id=run_id,
                code="collection_claim_error",
                message="Collection run could not be claimed",
                exception=exc,
            )
            return {
                "run_id": str(run_id),
                "status": CollectionStatus.FAILED.value,
                "error_code": "collection_claim_error",
            }

        try:
            resolved_proxy = _resolve_proxy(
                proxy_server,
                configured=runtime_settings.collector_proxy_server,
            )
            screenshot_directory = _optional_path(
                runtime_settings.collection_screenshot_directory
                or os.getenv("FARESCOPE_COLLECTION_SCREENSHOT_DIR")
            )
            async with collection_rate_gate.slot(claim.provider_code, claim.route_key):
                browser_result = await capture_runner.run(
                    BrowserRunConfig(
                        provider=claim.provider_code,
                        route_key=claim.route_key,
                        page_url=claim.page_url,
                        expected_capture_names=_EXPECTED_CAPTURES,
                        proxy_server=resolved_proxy,
                        browser_channel=runtime_settings.collector_browser_channel,
                        screenshot_directory=screenshot_directory,
                        post_capture_settle_seconds=(
                            runtime_settings.collection_capture_settle_seconds
                        ),
                    ),
                    capture_rules=ctrip_capture_rules(),
                )
        except Exception as exc:  # noqa: BLE001 - runtime/config exceptions need durable state
            await _record_unhandled_failure(
                session_factory,
                run_id=run_id,
                code="collector_runtime_error",
                message="Collector runtime raised an exception",
                exception=exc,
                expected_lease_owner=worker_lease_owner,
            )
            return {
                "run_id": str(run_id),
                "status": CollectionStatus.FAILED.value,
                "error_code": "collector_runtime_error",
            }

        if not browser_result.success:
            failure = _primary_failure(browser_result)
            try:
                async with session_factory() as session, session.begin():
                    run = await _load_run_for_update(session, run_id)
                    _assert_owned_lease(run, worker_lease_owner)
                    run.upstream_status = failure.kind.value
                    _clear_lease(run)
                    await record_collection_failure(
                        session,
                        run=run,
                        code=failure.kind.value,
                        message=failure.message,
                        retryable=failure.retryable,
                        diagnostics=(
                            _diagnostic_payload(item) for item in browser_result.diagnostics
                        ),
                        finished_at=browser_result.finished_at,
                    )
                    retry_at = _schedule_retry_if_eligible(
                        run,
                        retryable=failure.retryable,
                        failed_at=browser_result.finished_at,
                        base_seconds=runtime_settings.collection_retry_base_seconds,
                        maximum_seconds=runtime_settings.collection_retry_max_seconds,
                        jitter_ratio=runtime_settings.collection_retry_jitter_ratio,
                    )
            except CollectionRunUnavailableError as exc:
                return {
                    "run_id": str(run_id),
                    "status": "skipped",
                    "reason": str(exc),
                }
            return {
                "run_id": str(run_id),
                "status": (
                    CollectionStatus.PENDING.value
                    if retry_at is not None
                    else CollectionStatus.FAILED.value
                ),
                "error_code": failure.kind.value,
                "retryable": failure.retryable,
                "retry_scheduled_at": retry_at.isoformat() if retry_at is not None else None,
            }

        try:
            async with session_factory() as session, session.begin():
                run = await _load_run_for_update(session, run_id)
                _assert_owned_lease(run, worker_lease_owner)
                search_query = await _required_row(session, SearchQuery, claim.search_query_id)
                provider = await _required_row(session, Provider, claim.provider_id)
                persistence = await persist_collection_payloads(
                    session,
                    run=run,
                    provider=provider,
                    search_query=search_query,
                    captures=browser_result.captures,
                    observed_at=browser_result.finished_at,
                )
                if not persistence.has_data:
                    run.upstream_status = FailureKind.SCHEMA_MISSING.value
                    _clear_lease(run)
                    await record_collection_failure(
                        session,
                        run=run,
                        code="no_persistable_data",
                        message="Captured payloads contained no persistable fare data",
                        retryable=False,
                        diagnostics=(
                            {
                                "code": issue.code,
                                "message": issue.message,
                                "retryable": False,
                            }
                            for issue in persistence.diagnostics
                        ),
                        finished_at=browser_result.finished_at,
                    )
                    final_status = CollectionStatus.FAILED.value
                elif _requires_detail_retry(persistence):
                    run.upstream_status = "partial_fare_data"
                    run.error_code = "partial_fare_data"
                    run.error_message = (
                        "Calendar prices were captured but no detailed fare offers were available"
                    )
                    run.run_metadata = {
                        **(run.run_metadata or {}),
                        "partial_data": {
                            "calendar_count": persistence.calendar_count,
                            "itinerary_count": persistence.itinerary_count,
                            "offer_count": persistence.offer_count,
                            "price_observation_count": persistence.price_observation_count,
                            "retryable": run.attempt < run.max_attempts,
                        },
                    }
                    _clear_lease(run)
                    retry_at = _schedule_retry_if_eligible(
                        run,
                        retryable=True,
                        failed_at=browser_result.finished_at,
                        base_seconds=runtime_settings.collection_retry_base_seconds,
                        maximum_seconds=runtime_settings.collection_retry_max_seconds,
                        jitter_ratio=runtime_settings.collection_retry_jitter_ratio,
                    )
                    if retry_at is None:
                        run.upstream_status = "success_with_warnings"
                        await finalize_collection_success(
                            session,
                            run=run,
                            search_query=search_query,
                            result=persistence,
                            finished_at=browser_result.finished_at,
                        )
                        final_status = CollectionStatus.SUCCEEDED.value
                    else:
                        final_status = CollectionStatus.PENDING.value
                else:
                    run.upstream_status = (
                        "success_with_warnings" if persistence.diagnostics else "success"
                    )
                    _clear_lease(run)
                    await finalize_collection_success(
                        session,
                        run=run,
                        search_query=search_query,
                        result=persistence,
                        finished_at=browser_result.finished_at,
                    )
                    final_status = CollectionStatus.SUCCEEDED.value
        except CollectionRunUnavailableError as exc:
            return {
                "run_id": str(run_id),
                "status": "skipped",
                "reason": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 - persistence errors need a clean second transaction
            await _record_unhandled_failure(
                session_factory,
                run_id=run_id,
                code="persistence_error",
                message="Collection payload persistence failed",
                exception=exc,
                expected_lease_owner=worker_lease_owner,
            )
            return {
                "run_id": str(run_id),
                "status": CollectionStatus.FAILED.value,
                "error_code": "persistence_error",
            }

        return {
            "run_id": str(run_id),
            "status": final_status,
            "calendar_count": persistence.calendar_count,
            "itinerary_count": persistence.itinerary_count,
            "offer_count": persistence.offer_count,
            "price_observation_count": persistence.price_observation_count,
            "retry_scheduled_at": retry_at.isoformat() if retry_at is not None else None,
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


@celery_app.task(name="farescope.collection.run", ignore_result=False)
def collect_collection_run(
    collection_run_id: str,
    proxy_server: str | None = None,
    dispatch_token: str | None = None,
) -> dict[str, Any]:
    """Synchronous Celery wrapper around the async collection pipeline."""

    return asyncio.run(
        run_collection_once(
            collection_run_id,
            proxy_server=proxy_server,
            dispatch_token=dispatch_token,
        )
    )


async def _claim_collection_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    worker_id: str,
    dispatch_token: str | None,
    lease_seconds: int,
    browser_channel: str | None = None,
) -> CollectionClaim:
    now = datetime.now(UTC)
    if lease_seconds < 1:
        raise ValueError("collection run lease must be positive")
    run = await session.scalar(
        select(CollectionRun).where(CollectionRun.id == run_id).with_for_update()
    )
    if run is None:
        raise CollectionRunNotFoundError(str(run_id))
    if run.status in {
        CollectionStatus.SUCCEEDED.value,
        CollectionStatus.FAILED.value,
        CollectionStatus.CANCELED.value,
    }:
        raise CollectionRunUnavailableError(f"collection run is already {run.status}")
    if (
        run.status == CollectionStatus.PENDING.value
        and run.scheduled_at > now
    ):
        raise CollectionRunUnavailableError("collection run is scheduled for a future retry")
    active_lease = run.lease_expires_at is not None and run.lease_expires_at > now
    if run.status == CollectionStatus.RUNNING.value and active_lease:
        raise CollectionRunUnavailableError("collection run is already running")
    if (
        run.status == CollectionStatus.LEASED.value
        and active_lease
        and not dispatch_token_matches(
            lease_owner=run.lease_owner,
            dispatch_token=dispatch_token,
        )
    ):
        raise CollectionRunUnavailableError("collection dispatch token does not own the lease")
    if run.attempt >= run.max_attempts:
        raise CollectionRunUnavailableError("collection run exhausted its attempts")

    search_query = await _required_row(session, SearchQuery, run.search_query_id)
    provider = await _required_row(session, Provider, run.provider_id)
    if not provider.enabled:
        raise CollectionRunUnavailableError(f"provider is disabled: {provider.code}")
    if provider.code != "ctrip":
        raise CollectionRunUnavailableError(f"unsupported provider: {provider.code}")
    legs = (
        await session.scalars(
            select(SearchLeg)
            .where(SearchLeg.search_query_id == search_query.id)
            .order_by(SearchLeg.position)
        )
    ).all()
    page_url = build_ctrip_search_page_url(search_query, legs)
    route_key = f"{search_query.query_hash[:16]}:{legs[0].origin_code}-{legs[0].destination_code}"

    run.status = CollectionStatus.RUNNING.value
    run.attempt += 1
    run.lease_owner = f"worker:{worker_id}"[:160]
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.started_at = now
    run.finished_at = None
    run.error_code = None
    run.error_message = None
    run.run_metadata = {
        **(run.run_metadata or {}),
        "collector": {
            "page_url": page_url,
            "route_key": route_key,
            "expected_captures": sorted(_EXPECTED_CAPTURES),
            "browser_channel": browser_channel or "chromium",
        },
    }
    await session.flush()
    return CollectionClaim(
        run_id=run.id,
        search_query_id=search_query.id,
        provider_id=provider.id,
        provider_code=provider.code,
        route_key=route_key,
        page_url=page_url,
    )


def build_ctrip_search_page_url(
    search_query: SearchQuery,
    legs: list[SearchLeg] | tuple[SearchLeg, ...],
) -> str:
    """Build the public search page URL that causes Ctrip's own page requests."""

    expected_legs = 2 if search_query.trip_type == "round_trip" else 1
    if len(legs) != expected_legs:
        raise ValueError(f"{search_query.trip_type} collection requires {expected_legs} leg(s)")
    outbound = legs[0]
    prefix = "round" if expected_legs == 2 else "oneway"
    path = (
        f"https://flights.ctrip.com/online/list/{prefix}-"
        f"{outbound.origin_code.lower()}-{outbound.destination_code.lower()}"
    )
    query = {
        "depdate": outbound.departure_date.isoformat(),
        "cabin": "y_s_c_f",
        "adult": str(search_query.adults),
        "child": str(search_query.children),
        "infant": str(search_query.infants),
    }
    if expected_legs == 2:
        query["depdate"] = (
            f"{outbound.departure_date.isoformat()}_{legs[1].departure_date.isoformat()}"
        )
    return f"{path}?{urlencode(query)}"


async def _record_unhandled_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    code: str,
    message: str,
    exception: Exception,
    expected_lease_owner: str | None = None,
) -> None:
    try:
        async with session_factory() as session, session.begin():
            run = await _load_run_for_update(session, run_id)
            if expected_lease_owner is not None:
                _assert_owned_lease(run, expected_lease_owner)
            run.upstream_status = code
            _clear_lease(run)
            await record_collection_failure(
                session,
                run=run,
                code=code,
                message=message,
                retryable=False,
                diagnostics=(
                    {
                        "code": code,
                        "message": message,
                        "exception_type": type(exception).__name__,
                    },
                ),
            )
    except Exception:  # noqa: BLE001 - original failure must remain the task outcome
        return


async def _load_run_for_update(session: AsyncSession, run_id: UUID) -> CollectionRun:
    run = await session.scalar(
        select(CollectionRun).where(CollectionRun.id == run_id).with_for_update()
    )
    if run is None:
        raise CollectionRunNotFoundError(str(run_id))
    return run


async def _required_row(
    session: AsyncSession,
    model: type[Any],
    row_id: UUID,
) -> Any:
    row = await session.get(model, row_id)
    if row is None:
        raise RuntimeError(f"missing {model.__name__}: {row_id}")
    return row


def _resolve_proxy(value: str | None, *, configured: str | None = None) -> str | None:
    candidate = value or configured or os.getenv("FARESCOPE_CTRIP_PROXY")
    if not candidate:
        return None
    candidate = candidate.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        raise ValueError("collector proxy must be an HTTP(S) or SOCKS5 URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("collector proxy credentials must not be embedded in the URL")
    return candidate


def _optional_path(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    return Path(value.strip()).resolve()


def _primary_failure(result: BrowserRunResult) -> CaptureDiagnostic:
    priority = {
        FailureKind.ANTI_BOT_432: 0,
        FailureKind.BROWSER_UNAVAILABLE: 1,
        FailureKind.SCHEMA_MISSING: 2,
        FailureKind.RESPONSE_DECODE: 3,
        FailureKind.RESPONSE_STATUS: 4,
        FailureKind.TIMEOUT: 5,
        FailureKind.NAVIGATION_ERROR: 6,
        FailureKind.INTERNAL_ERROR: 7,
        FailureKind.SCREENSHOT_FAILED: 8,
    }
    if result.diagnostics:
        return min(result.diagnostics, key=lambda item: priority.get(item.kind, 99))
    return CaptureDiagnostic(
        kind=FailureKind.INTERNAL_ERROR,
        message="Collection ended without required captures",
        provider=result.provider,
        route_key=result.route_key,
        retryable=True,
    )


def _diagnostic_payload(value: CaptureDiagnostic) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": value.kind.value,
        "message": value.message,
        "capture_name": value.capture_name,
        "status_code": value.status_code,
        "retryable": value.retryable,
    }
    safe_details = {
        key: value.details[key]
        for key in ("browser_channel", "exception_type")
        if key in value.details
    }
    if safe_details:
        payload["details"] = safe_details
    return payload


def _clear_lease(run: CollectionRun) -> None:
    run.lease_owner = None
    run.lease_expires_at = None


def _assert_owned_lease(
    run: CollectionRun,
    lease_owner: str,
    *,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    if (
        run.status != CollectionStatus.RUNNING.value
        or run.lease_owner != lease_owner
        or run.lease_expires_at is None
        or run.lease_expires_at <= current_time
    ):
        raise CollectionRunUnavailableError("collection run lease is no longer owned")


def _requires_detail_retry(result: Any) -> bool:
    """Retry page sessions that yielded a calendar but no actionable fare offer."""

    return result.calendar_count > 0 and result.price_observation_count == 0


def _schedule_retry_if_eligible(
    run: CollectionRun,
    *,
    retryable: bool,
    failed_at: datetime,
    base_seconds: int,
    maximum_seconds: int,
    jitter_ratio: float = 0.0,
    random_fraction: float | None = None,
) -> datetime | None:
    """Return a failed run to pending with bounded exponential backoff."""

    if base_seconds < 1 or maximum_seconds < base_seconds:
        raise ValueError("invalid collection retry delay bounds")
    if not retryable or run.attempt >= run.max_attempts:
        return None

    policy = RetryPolicy(
        max_attempts=run.max_attempts,
        initial_delay_seconds=base_seconds,
        multiplier=2,
        maximum_delay_seconds=maximum_seconds,
        jitter_ratio=jitter_ratio,
    )
    delay = policy.delay_after(
        run.attempt,
        random_fraction=random.random() if random_fraction is None else random_fraction,
    )
    delay_seconds = int(math.ceil(delay))
    retry_at = failed_at + timedelta(seconds=delay_seconds)
    retry_metadata = dict((run.run_metadata or {}).get("retry") or {})
    run.status = CollectionStatus.PENDING.value
    run.scheduled_at = retry_at
    run.started_at = None
    run.finished_at = None
    run.run_metadata = {
        **(run.run_metadata or {}),
        "retry": {
            **retry_metadata,
            "scheduled_count": int(retry_metadata.get("scheduled_count", 0)) + 1,
            "last_failed_at": failed_at.isoformat(),
            "next_attempt_at": retry_at.isoformat(),
            "delay_seconds": delay_seconds,
            "jitter_ratio": jitter_ratio,
        },
    }
    return retry_at

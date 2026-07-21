"""Celery tasks for alert evaluation and notification delivery."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import create_engine, create_session_factory
from app.models import CollectionRun
from app.models.enums import CollectionStatus
from app.security import InvalidEncryptionKeyError, SecretBox
from app.services.alerts import evaluate_collection_run
from app.services.notification_delivery import (
    claim_pending_deliveries,
    deliver_work,
    finish_delivery,
)
from app.settings import Settings, get_settings
from app.tasks.celery_app import celery_app


async def evaluate_pending_alerts_once(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
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
            application_name="farescope-alerts",
        )
        session_factory = create_session_factory(owned_engine)
    evaluated = 0
    events = 0
    deliveries = 0
    try:
        async with session_factory() as session:
            run_ids = list(
                (
                    await session.scalars(
                        select(CollectionRun.id)
                        .where(
                            CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                            CollectionRun.alerts_evaluated_at.is_(None),
                        )
                        .order_by(CollectionRun.finished_at, CollectionRun.id)
                        .limit(min(limit, 200))
                    )
                ).all()
            )
        for run_id in run_ids:
            async with session_factory() as session, session.begin():
                result = await evaluate_collection_run(session, run_id=run_id)
            evaluated += result.evaluated_rules
            events += result.created_events
            deliveries += result.created_deliveries
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()
    return {
        "runs": len(run_ids) if "run_ids" in locals() else 0,
        "evaluated_rules": evaluated,
        "created_events": events,
        "created_deliveries": deliveries,
    }


async def deliver_pending_notifications_once(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    runtime_settings = settings or get_settings()
    key = runtime_settings.secret_encryption_key
    if key is None:
        return {"claimed": 0, "succeeded": 0, "failed": 0, "disabled": True}
    try:
        secret_box = SecretBox(key.get_secret_value())
    except InvalidEncryptionKeyError:
        return {"claimed": 0, "succeeded": 0, "failed": 0, "disabled": True}

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
            application_name="farescope-notifications",
        )
        session_factory = create_session_factory(owned_engine)
    claimed = succeeded = failed = 0
    try:
        async with session_factory() as session, session.begin():
            works = await claim_pending_deliveries(
                session,
                secret_box=secret_box,
                limit=limit or runtime_settings.notification_delivery_batch_size,
                stale_after_seconds=runtime_settings.notification_delivery_stale_seconds,
            )
        claimed = len(works)
        for work in works:
            result = await deliver_work(
                work,
                timeout_seconds=runtime_settings.notification_delivery_timeout_seconds,
            )
            async with session_factory() as session, session.begin():
                await finish_delivery(
                    session,
                    delivery_id=work.delivery_id,
                    result=result,
                    max_attempts=runtime_settings.notification_delivery_max_attempts,
                    retry_base_seconds=runtime_settings.notification_retry_base_seconds,
                )
            if result.success:
                succeeded += 1
            else:
                failed += 1
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()
    return {"claimed": claimed, "succeeded": succeeded, "failed": failed, "disabled": False}


@celery_app.task(name="farescope.alerts.evaluate_pending", ignore_result=False)
def evaluate_pending_alerts() -> dict[str, Any]:
    return asyncio.run(evaluate_pending_alerts_once())


@celery_app.task(name="farescope.notifications.deliver_pending", ignore_result=False)
def deliver_pending_notifications() -> dict[str, Any]:
    return asyncio.run(deliver_pending_notifications_once())

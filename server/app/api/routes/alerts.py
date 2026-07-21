from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import DatabaseSession, IdentityDependency, require_csrf
from app.api.pagination import (
    InvalidCursorError,
    TimestampCursor,
    decode_timestamp_cursor,
    encode_timestamp_cursor,
)
from app.api.schemas.alerts import (
    AlertEventListResponse,
    AlertEventPublic,
    AlertRuleCreateRequest,
    AlertRuleListResponse,
    AlertRulePublic,
    AlertRuleUpdateRequest,
    NotificationDeliveryListResponse,
    NotificationDeliveryPublic,
)
from app.services.alerts import (
    AlertConfigurationError,
    AlertConflictError,
    AlertNotFoundError,
    create_alert_rule,
    delete_alert_rule,
    list_alert_events,
    list_alert_rules,
    list_notification_deliveries,
    update_alert_rule,
)

router = APIRouter()


def _rule_public(view) -> AlertRulePublic:
    rule = view.rule
    return AlertRulePublic(
        id=rule.id,
        subscriptionId=rule.subscription_id,
        name=rule.name,
        ruleType=rule.rule_type,
        enabled=rule.enabled,
        severity=rule.severity,
        thresholdPriceMinor=rule.threshold_price_minor,
        thresholdCurrency=rule.threshold_currency,
        thresholdPercentage=rule.threshold_percentage,
        comparisonWindowDays=rule.comparison_window_days,
        cooldownSeconds=rule.cooldown_seconds,
        channelIds=list(view.channel_ids),
        createdAt=rule.created_at,
        updatedAt=rule.updated_at,
    )


@router.get("/alerts/rules", response_model=AlertRuleListResponse)
async def get_alert_rules(
    identity: IdentityDependency,
    database: DatabaseSession,
    subscription_id: Annotated[UUID | None, Query(alias="subscriptionId")] = None,
) -> AlertRuleListResponse:
    items = await list_alert_rules(
        database,
        user_id=identity.user.id,
        subscription_id=subscription_id,
    )
    return AlertRuleListResponse(items=[_rule_public(item) for item in items])


@router.post(
    "/alerts/rules",
    response_model=AlertRulePublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def add_alert_rule(
    payload: AlertRuleCreateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> AlertRulePublic:
    try:
        async with database.begin():
            view = await create_alert_rule(
                database,
                user=identity.user,
                subscription_id=payload.subscription_id,
                name=payload.name,
                rule_type=payload.rule_type,
                enabled=payload.enabled,
                threshold_price_minor=payload.threshold_price_minor,
                threshold_currency=payload.threshold_currency,
                threshold_percentage=payload.threshold_percentage,
                comparison_window_days=payload.comparison_window_days,
                cooldown_seconds=payload.cooldown_seconds,
                channel_ids=payload.channel_ids,
                rule_config=payload.rule_config,
            )
    except AlertNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (AlertConfigurationError, AlertConflictError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return _rule_public(view)


@router.patch(
    "/alerts/rules/{rule_id}",
    response_model=AlertRulePublic,
    dependencies=[Depends(require_csrf)],
)
async def edit_alert_rule(
    rule_id: UUID,
    payload: AlertRuleUpdateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> AlertRulePublic:
    updates = payload.model_dump(exclude_unset=True)
    try:
        async with database.begin():
            view = await update_alert_rule(
                database,
                user=identity.user,
                rule_id=rule_id,
                updates=updates,
            )
    except AlertNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (AlertConfigurationError, AlertConflictError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return _rule_public(view)


@router.delete(
    "/alerts/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def remove_alert_rule(
    rule_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> Response:
    try:
        async with database.begin():
            await delete_alert_rule(database, user_id=identity.user.id, rule_id=rule_id)
    except AlertNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts/events", response_model=AlertEventListResponse)
async def get_alert_events(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> AlertEventListResponse:
    as_of = datetime.now(UTC)
    before_created_at = before_id = None
    if cursor:
        try:
            decoded = decode_timestamp_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error
        as_of = decoded.as_of
        before_created_at, before_id = decoded.timestamp, decoded.row_id
        if before_created_at > as_of:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pagination cursor is outside its alert snapshot",
            )
    items, has_more = await list_alert_events(
        database,
        user_id=identity.user.id,
        limit=limit,
        as_of=as_of,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = None
    if has_more and items:
        event = items[-1].event
        next_cursor = encode_timestamp_cursor(
            TimestampCursor(
                as_of=as_of, timestamp=event.created_at, row_id=event.id
            )
        )
    return AlertEventListResponse(
        items=[
            AlertEventPublic(
                id=item.event.id,
                alertRuleId=item.event.alert_rule_id,
                subscriptionId=item.subscription_id,
                collectionRunId=item.event.collection_run_id,
                eventType=item.event.event_type,
                severity=item.event.severity,
                title=item.event.title,
                body=item.event.body,
                eventPayload=item.event.event_payload,
                suppressedAt=item.event.suppressed_at,
                createdAt=item.event.created_at,
            )
            for item in items
        ],
        nextCursor=next_cursor,
    )


@router.get("/alerts/deliveries", response_model=NotificationDeliveryListResponse)
async def get_alert_deliveries(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: int = Query(default=100, ge=1, le=200),
) -> NotificationDeliveryListResponse:
    items = await list_notification_deliveries(
        database,
        user_id=identity.user.id,
        limit=limit,
    )
    return NotificationDeliveryListResponse(
        items=[
            NotificationDeliveryPublic(
                id=item.id,
                alertEventId=item.alert_event_id,
                notificationChannelId=item.notification_channel_id,
                status=item.status,
                attemptCount=item.attempt_count,
                nextAttemptAt=item.next_attempt_at,
                sentAt=item.sent_at,
                errorCode=item.error_code,
                errorMessage=item.error_message,
                updatedAt=item.updated_at,
            )
            for item in items
        ]
    )

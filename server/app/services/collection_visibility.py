from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, or_, select

from app.models import CollectionRun, Subscription


def visible_collection_run_condition(user_id: UUID, *, enabled_subscriptions: bool = False):
    """Runs visible to a user through subscriptions or their on-demand searches."""

    subscription_filters = [
        Subscription.user_id == user_id,
        Subscription.search_query_id == CollectionRun.search_query_id,
    ]
    if enabled_subscriptions:
        subscription_filters.append(Subscription.enabled.is_(True))
    subscribed_scope = exists(
        select(Subscription.id).where(*subscription_filters)
    )
    on_demand_scope = CollectionRun.run_metadata.contains(
        {"on_demand_user_ids": [str(user_id)]}
    )
    return or_(subscribed_scope, on_demand_scope)

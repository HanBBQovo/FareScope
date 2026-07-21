from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search import Subscription


def user_subscriptions_statement(user_id: UUID) -> Select[tuple[Subscription]]:
    return (
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )


async def list_user_subscriptions(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[Subscription]:
    return (await session.scalars(user_subscriptions_statement(user_id))).all()


async def get_user_subscription(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID,
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id,
        )
    )

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.search import FareSearch
from app.models.search import SearchLeg, SearchQuery


def canonical_search_values(search: FareSearch) -> dict[str, object]:
    return {
        "provider": search.provider,
        "query_hash": search.query_hash,
        "trip_type": search.trip_type.value,
        "adults": search.passengers.adults,
        "children": search.passengers.children,
        "infants": search.passengers.infants,
        "cabin": search.cabin.value,
        "currency": search.currency,
        "direct_only": search.filters.direct_only,
        "normalized_query": search.canonical_payload(),
    }


async def get_or_create_canonical_search(
    session: AsyncSession,
    search: FareSearch,
) -> tuple[SearchQuery, bool]:
    """Return one shared search row for all users with the same canonical query."""

    query_id = uuid4()
    statement = (
        insert(SearchQuery)
        .values(
            id=query_id,
            **canonical_search_values(search),
        )
        .on_conflict_do_nothing(index_elements=[SearchQuery.query_hash])
        .returning(SearchQuery.id)
    )
    created_id = (await session.execute(statement)).scalar_one_or_none()

    if created_id is not None:
        session.add_all(
            SearchLeg(
                search_query_id=created_id,
                position=position,
                origin_code=leg.origin,
                destination_code=leg.destination,
                departure_date=leg.departure_date,
            )
            for position, leg in enumerate(search.legs)
        )
        await session.flush()
        created = await session.get(SearchQuery, created_id)
        if created is None:
            raise RuntimeError("Created canonical search could not be reloaded")
        return created, True

    existing = await session.scalar(
        select(SearchQuery).where(SearchQuery.query_hash == search.query_hash)
    )
    if existing is None:
        raise RuntimeError("Canonical search conflict resolved without a visible row")
    return existing, False

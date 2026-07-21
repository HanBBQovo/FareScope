from datetime import date
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.domain.search import FareSearch, SearchFilters, SearchLeg, TripType
from app.repositories.canonical_searches import canonical_search_values
from app.repositories.subscriptions import user_subscriptions_statement


def test_canonical_search_values_reuse_domain_hash_boundary() -> None:
    search = FareSearch(
        trip_type=TripType.ONE_WAY,
        legs=(SearchLeg(origin="SHA", destination="TYO", departure_date=date(2026, 8, 15)),),
        filters=SearchFilters(
            direct_only=True,
            airline_codes=("MU",),
            departure_airports=("PVG",),
        ),
    )

    values = canonical_search_values(search)

    assert values["query_hash"] == search.query_hash
    assert values["normalized_query"] == search.canonical_payload()
    assert values["direct_only"] is True
    assert values["normalized_query"]["filters"] == {"direct_only": True}
    assert search.local_filter_payload()["airline_codes"] == ["MU"]


def test_subscription_query_is_always_owner_scoped() -> None:
    user_id = uuid4()
    statement = user_subscriptions_statement(user_id)
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )

    assert "subscriptions.user_id" in str(compiled)
    assert str(user_id) in str(compiled)

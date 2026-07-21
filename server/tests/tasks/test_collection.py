from __future__ import annotations

from datetime import date

import pytest

from app.models import SearchLeg, SearchQuery
from app.tasks.collection import _resolve_proxy, build_ctrip_search_page_url


def test_builds_public_one_way_and_round_trip_urls() -> None:
    one_way = _query("one_way")
    outbound = _leg(position=0, origin="SHA", destination="TYO", value=date(2026, 8, 15))

    assert build_ctrip_search_page_url(one_way, [outbound]) == (
        "https://flights.ctrip.com/online/list/oneway-sha-tyo?depdate=2026-08-15&cabin=y_s_c_f"
        "&adult=1&child=0&infant=0"
    )

    round_trip = _query("round_trip")
    inbound = _leg(position=1, origin="TYO", destination="SHA", value=date(2026, 8, 22))
    assert build_ctrip_search_page_url(round_trip, [outbound, inbound]) == (
        "https://flights.ctrip.com/online/list/round-sha-tyo?"
        "depdate=2026-08-15_2026-08-22&cabin=y_s_c_f&adult=1&child=0&infant=0"
    )


def test_proxy_resolution_rejects_embedded_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARESCOPE_CTRIP_PROXY", "http://127.0.0.1:7890")
    assert _resolve_proxy(None) == "http://127.0.0.1:7890"
    assert _resolve_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    with pytest.raises(ValueError, match="credentials"):
        _resolve_proxy("http://user:secret@proxy.example:8080")


def _query(trip_type: str) -> SearchQuery:
    return SearchQuery(
        provider="ctrip",
        query_hash="a" * 64,
        trip_type=trip_type,
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="CNY",
        direct_only=False,
        normalized_query={},
    )


def _leg(*, position: int, origin: str, destination: str, value: date) -> SearchLeg:
    return SearchLeg(
        position=position,
        origin_code=origin,
        destination_code=destination,
        departure_date=value,
    )

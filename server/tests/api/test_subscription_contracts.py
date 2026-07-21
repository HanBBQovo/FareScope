from app.api.schemas.subscriptions import SubscriptionCreateRequest


def test_target_price_and_result_price_filter_are_distinct() -> None:
    payload = SubscriptionCreateRequest.model_validate(
        {
            "name": "Shanghai to Tokyo",
            "target_price_minor": 300_000,
            "search": {
                "trip_type": "one_way",
                "legs": [
                    {
                        "origin": "SHA",
                        "destination": "TYO",
                        "departure_date": "2026-09-10",
                    }
                ],
                "filters": {"max_price_minor": 450_000},
            },
        }
    )

    assert payload.target_price_minor == 300_000
    assert payload.search.filters.max_price_minor == 450_000

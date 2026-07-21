from types import SimpleNamespace

from app.services.alerts import _evaluate_rule


def _rule(rule_type: str, **values: object) -> SimpleNamespace:
    return SimpleNamespace(
        rule_type=rule_type,
        threshold_price_minor=values.get("threshold_price_minor"),
        threshold_currency=values.get("threshold_currency"),
        threshold_percentage=values.get("threshold_percentage"),
        rule_config=values.get("rule_config", {}),
    )


def _query() -> SimpleNamespace:
    return SimpleNamespace(
        currency="CNY",
        query_hash="route-hash",
        normalized_query={"label": "SHA-TYO"},
    )


def test_price_threshold_uses_minor_units_and_currency() -> None:
    triggered, event_type, _, _ = _evaluate_rule(
        _rule("price_threshold", threshold_price_minor=12000),
        query=_query(),
        current_price=11900,
        current_currency="CNY",
        previous_minimum=None,
        direct=False,
    )

    assert triggered is True
    assert event_type == "price_threshold"


def test_percentage_drop_requires_a_previous_observation() -> None:
    rule = _rule("percentage_drop", threshold_percentage=1000)
    assert _evaluate_rule(
        rule,
        query=_query(),
        current_price=9000,
        current_currency="CNY",
        previous_minimum=None,
        direct=False,
    )[0] is False
    assert _evaluate_rule(
        rule,
        query=_query(),
        current_price=9000,
        current_currency="CNY",
        previous_minimum=10000,
        direct=False,
    )[0] is True


def test_round_trip_range_is_configured_explicitly() -> None:
    triggered, event_type, _, _ = _evaluate_rule(
        _rule(
            "round_trip_range",
            rule_config={"minPriceMinor": 20_000, "maxPriceMinor": 30_000},
        ),
        query=_query(),
        current_price=25_000,
        current_currency="CNY",
        previous_minimum=None,
        direct=False,
    )

    assert triggered is True
    assert event_type == "round_trip_range"

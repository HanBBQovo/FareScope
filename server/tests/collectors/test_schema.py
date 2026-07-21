from copy import deepcopy

from app.collectors.schema import schema_fingerprint, schema_shape


def test_schema_fingerprint_ignores_values_and_list_order() -> None:
    first = {"data": [{"date": "2026-01-01", "price": 100}, {"error": None}]}
    second = {"data": [{"error": None}, {"date": "2030-12-31", "price": 9999}]}

    assert schema_fingerprint(first) == schema_fingerprint(second)


def test_schema_fingerprint_changes_when_field_type_drifts() -> None:
    payload = {"data": [{"date": "2026-01-01", "price": 100}]}
    drifted = deepcopy(payload)
    drifted["data"][0]["price"] = {"amount": 100}

    assert schema_fingerprint(payload) != schema_fingerprint(drifted)
    assert schema_shape(payload)["data"]["list"][0]["price"] == "number"

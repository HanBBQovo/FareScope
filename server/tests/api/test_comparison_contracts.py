from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.comparisons import (
    ComparisonViewCreateRequest,
    ComparisonViewReplaceRequest,
)
from app.services.comparisons import (
    comparison_request_fingerprint,
    normalize_comparison_name,
)


def test_comparison_create_contract_normalizes_name_and_preserves_route_order() -> None:
    route_ids = [uuid4(), uuid4()]
    payload = ComparisonViewCreateRequest.model_validate(
        {
            "name": "  Japan   autumn  ",
            "subscriptionIds": [str(value) for value in route_ids],
            "trendDays": 30,
            "idempotencyKey": "comparison:create:1",
        }
    )

    assert payload.name == "Japan autumn"
    assert payload.subscription_ids == route_ids
    assert payload.trend_days == 30


@pytest.mark.parametrize("trend_days", (7, 30, 90))
def test_comparison_contract_accepts_only_supported_trend_windows(trend_days: int) -> None:
    routes = [uuid4(), uuid4()]
    payload = ComparisonViewReplaceRequest.model_validate(
        {
            "name": "Window",
            "subscriptionIds": routes,
            "trendDays": trend_days,
            "expectedVersion": 1,
        }
    )
    assert payload.trend_days == trend_days


@pytest.mark.parametrize("trend_days", (1, 14, 365))
def test_comparison_contract_rejects_unsupported_trend_windows(trend_days: int) -> None:
    with pytest.raises(ValidationError):
        ComparisonViewReplaceRequest.model_validate(
            {
                "name": "Window",
                "subscriptionIds": [uuid4(), uuid4()],
                "trendDays": trend_days,
                "expectedVersion": 1,
            }
        )


def test_comparison_contract_rejects_duplicate_or_too_many_routes() -> None:
    route_id = uuid4()
    with pytest.raises(ValidationError, match="must be unique"):
        ComparisonViewCreateRequest.model_validate(
            {
                "name": "Duplicate",
                "subscriptionIds": [route_id, route_id],
                "idempotencyKey": "comparison:duplicate",
            }
        )
    with pytest.raises(ValidationError):
        ComparisonViewCreateRequest.model_validate(
            {
                "name": "Too many",
                "subscriptionIds": [uuid4() for _ in range(9)],
                "idempotencyKey": "comparison:too-many",
            }
        )


def test_comparison_name_and_request_fingerprint_are_canonical() -> None:
    routes = (uuid4(), uuid4())
    assert normalize_comparison_name("  Tokyo   routes ") == ("Tokyo routes", "tokyo routes")
    assert comparison_request_fingerprint(
        name="Tokyo routes",
        subscription_ids=routes,
        trend_days=30,
    ) == comparison_request_fingerprint(
        name="  Tokyo   routes ",
        subscription_ids=routes,
        trend_days=30,
    )
    assert comparison_request_fingerprint(
        name="Tokyo routes",
        subscription_ids=routes,
        trend_days=30,
    ) != comparison_request_fingerprint(
        name="Tokyo routes",
        subscription_ids=tuple(reversed(routes)),
        trend_days=30,
    )

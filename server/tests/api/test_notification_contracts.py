import pytest
from pydantic import ValidationError

from app.api.schemas.notifications import NotificationChannelCreateRequest
from app.main import create_app


def test_notification_channel_contract_exposes_schedule_without_secret_fields() -> None:
    schema = create_app().openapi()["components"]["schemas"]
    create_fields = schema["NotificationChannelCreateRequest"]["properties"]
    update_fields = schema["NotificationChannelUpdateRequest"]["properties"]
    public_fields = schema["NotificationChannelPublic"]["properties"]
    schedule_fields = {
        "timezone",
        "quietHoursStart",
        "quietHoursEnd",
        "allowedWeekdays",
    }

    assert schedule_fields.issubset(create_fields)
    assert schedule_fields.issubset(update_fields)
    assert schedule_fields.issubset(public_fields)
    assert "destination" not in public_fields
    assert "secret" not in public_fields
    assert "secretCiphertext" not in public_fields


def test_notification_channel_create_validates_iana_timezone_and_quiet_pair() -> None:
    payload = NotificationChannelCreateRequest.model_validate(
        {
            "type": "webhook",
            "label": "Tokyo daytime",
            "destination": "https://hooks.example.test/fare",
            "timezone": "Asia/Tokyo",
            "quietHoursStart": "22:00",
            "quietHoursEnd": "07:30",
            "allowedWeekdays": [0, 1, 2, 3, 4],
        }
    )

    assert payload.timezone == "Asia/Tokyo"
    assert payload.quiet_hours_start is not None
    assert payload.quiet_hours_start.hour == 22
    assert payload.allowed_weekdays == [0, 1, 2, 3, 4]

    with pytest.raises(ValidationError, match="valid IANA"):
        NotificationChannelCreateRequest.model_validate(
            {
                "type": "webhook",
                "label": "Invalid timezone",
                "destination": "https://hooks.example.test/fare",
                "timezone": "Not/AZone",
                "allowedWeekdays": [0],
            }
        )

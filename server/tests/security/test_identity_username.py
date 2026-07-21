import pytest
from pydantic import ValidationError

from app.api.schemas.identity import LoginRequest, RegisterRequest
from app.services.identity import normalize_username


def test_normalize_username_is_case_insensitive_and_trimmed() -> None:
    assert normalize_username("  Flight_Observer-01 ") == "flight_observer-01"
    assert LoginRequest(username="  Flight_Observer-01 ", password="x").username == (
        "flight_observer-01"
    )


@pytest.mark.parametrize(
    "username",
    ["ab", "has space", "-starts-with-symbol", "中文用户名", "a" * 65],
)
def test_invalid_usernames_are_rejected(username: str) -> None:
    with pytest.raises(ValueError):
        normalize_username(username)
    with pytest.raises(ValidationError):
        RegisterRequest(username=username, password="a")


def test_registration_schema_has_no_email_or_display_name_fields() -> None:
    fields = set(RegisterRequest.model_fields)
    assert fields == {"username", "password"}
    with pytest.raises(ValidationError):
        RegisterRequest(username="observer", password="a", email="contact@example.test")

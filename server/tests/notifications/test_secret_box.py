import pytest
from cryptography.fernet import Fernet

from app.security import SecretBox
from app.services.notification_channels import (
    NotificationChannelError,
    _normalize_destination,
    mask_destination,
)


def test_secret_box_round_trip_does_not_store_plaintext() -> None:
    box = SecretBox(Fernet.generate_key().decode("ascii"))
    ciphertext = box.encrypt_mapping({"destination": "alerts@example.com"})

    assert b"alerts@example.com" not in ciphertext
    assert box.decrypt_mapping(ciphertext) == {"destination": "alerts@example.com"}


def test_destination_masking_preserves_only_safe_context() -> None:
    assert mask_destination("email", "alerts@example.com") == "a***@example.com"
    assert (
        mask_destination("webhook", "https://hooks.example.com/private/path?token=secret")
        == "https://hooks.example.com/***"
    )
    assert mask_destination("telegram", "123456789") == "***6789"


def test_unconfigured_email_and_private_webhook_are_rejected() -> None:
    with pytest.raises(NotificationChannelError, match="email delivery"):
        _normalize_destination("email", "alerts@example.com")
    with pytest.raises(NotificationChannelError, match="not allowed"):
        _normalize_destination("webhook", "https://127.0.0.1/hook")
    assert _normalize_destination("telegram", "123:ABC|@fare_scope") == "123:ABC|@fare_scope"

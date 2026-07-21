import pytest

from app.security.passwords import hash_password, password_needs_rehash, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("incorrect password", encoded)
    assert not password_needs_rehash(encoded)


def test_short_password_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        hash_password("123")


def test_four_character_password_is_accepted() -> None:
    encoded = hash_password("1234")

    assert verify_password("1234", encoded)


def test_unknown_hash_is_handled_as_invalid() -> None:
    assert not verify_password("any password", "not-a-password-hash")
    assert password_needs_rehash("not-a-password-hash")

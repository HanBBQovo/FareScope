from app.security.tokens import issue_secret_token, token_digest


def test_issued_token_only_matches_its_digest() -> None:
    token = issue_secret_token("fs_session")
    another = issue_secret_token("fs_session")

    assert token.value.startswith("fs_session_")
    assert token.value not in token.digest
    assert token_digest(token.value) == token.digest
    assert token.digest != another.digest


def test_token_entropy_floor_is_enforced() -> None:
    try:
        issue_secret_token("fs_invite", entropy_bytes=16)
    except ValueError as error:
        assert "192 bits" in str(error)
    else:
        raise AssertionError("weak token was accepted")

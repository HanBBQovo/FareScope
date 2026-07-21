import pytest

from performance.safety import (
    DISPOSABLE_CONFIRMATION,
    redact_url,
    require_confirmation,
    validate_database_name,
    validate_performance_database_url,
)


def test_disposable_confirmation_is_exact() -> None:
    require_confirmation(DISPOSABLE_CONFIRMATION)

    with pytest.raises(ValueError, match="confirmation does not match"):
        require_confirmation("yes")


@pytest.mark.parametrize(
    "name",
    (
        "farescope",
        "farescope_perf",
        "farescope_perf-unsafe",
        "FARESCOPE_PERF_REFERENCE",
        "farescope_perf_reference;drop_database",
    ),
)
def test_performance_database_name_rejects_non_disposable_targets(name: str) -> None:
    with pytest.raises(ValueError):
        validate_database_name(name)


def test_performance_database_url_requires_safe_name_and_redacts_password() -> None:
    safe_url = "postgresql://fare:secret@127.0.0.1:5432/farescope_perf_reference"

    assert validate_performance_database_url(safe_url) == safe_url
    assert redact_url(safe_url) == (
        "postgresql://fare:***@127.0.0.1:5432/farescope_perf_reference"
    )

    with pytest.raises(ValueError, match="must start"):
        validate_performance_database_url(
            "postgresql://fare:secret@127.0.0.1:5432/farescope"
        )

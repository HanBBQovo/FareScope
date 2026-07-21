from datetime import datetime

import pytest
from pydantic import ValidationError

from app.api.schemas.exports import ExportJobCreateRequest
from app.main import create_app


def test_export_api_has_bounded_background_job_contract() -> None:
    schema = create_app().openapi()
    collection = schema["paths"]["/api/exports"]
    job = schema["paths"]["/api/exports/{job_id}"]
    download = schema["paths"]["/api/exports/{job_id}/download"]

    assert set(collection) == {"get", "post"}
    assert set(job) == {"get", "delete"}
    assert set(download) == {"get"}
    assert collection["post"]["responses"]["202"]
    assert job["delete"]["responses"]["202"]
    list_parameters = {item["name"]: item for item in collection["get"]["parameters"]}
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert {"subscriptionId", "cursor"}.issubset(list_parameters)

    fields = schema["components"]["schemas"]["ExportJobPublic"]["properties"]
    assert fields["scope"]["const"] == "canonical_query"
    assert {
        "attempt",
        "maxAttempts",
        "processedRows",
        "rowCount",
        "sizeBytes",
        "checksumSha256",
        "errorCode",
        "expiresAt",
        "downloadReady",
        "snapshotAt",
    }.issubset(fields)


def test_export_request_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        ExportJobCreateRequest(
            subscriptionId="12345678-1234-1234-1234-123456789012",
            format="csv",
            rangeStart=datetime(2026, 7, 1),
            rangeEnd=datetime(2026, 7, 2),
            idempotencyKey="12345678-1234-1234-1234-123456789012",
        )

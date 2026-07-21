from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.api.schemas.fares import ResponseMeta


class ExportJobCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subscription_id: UUID = Field(alias="subscriptionId")
    format: Literal["csv", "json"]
    range_start: AwareDatetime = Field(alias="rangeStart")
    range_end: AwareDatetime = Field(alias="rangeEnd")
    idempotency_key: Annotated[
        str,
        Field(
            alias="idempotencyKey",
            min_length=8,
            max_length=80,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ]


class ExportJobPublic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    subscription_id: UUID | None = Field(alias="subscriptionId")
    format: Literal["csv", "json"]
    scope: Literal["canonical_query"] = "canonical_query"
    status: Literal["pending", "running", "succeeded", "failed", "expired", "deleting"]
    range_start: datetime = Field(alias="rangeStart")
    range_end: datetime = Field(alias="rangeEnd")
    snapshot_at: datetime = Field(alias="snapshotAt")
    attempt: int
    max_attempts: int = Field(alias="maxAttempts")
    processed_rows: int = Field(alias="processedRows")
    row_count: int | None = Field(alias="rowCount")
    size_bytes: int | None = Field(alias="sizeBytes")
    checksum_sha256: str | None = Field(alias="checksumSha256")
    file_name: str | None = Field(alias="fileName")
    error_code: str | None = Field(alias="errorCode")
    error_message: str | None = Field(alias="errorMessage")
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")
    expires_at: datetime | None = Field(alias="expiresAt")
    download_ready: bool = Field(alias="downloadReady")


class ExportJobListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: ResponseMeta
    items: list[ExportJobPublic]
    has_more: bool = Field(alias="hasMore")
    next_cursor: str | None = Field(alias="nextCursor")

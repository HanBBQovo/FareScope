from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import CurrentIdentity, get_current_identity, get_database_session
from app.db.partitions import price_observation_partition_ddl
from app.main import create_app
from app.models import (
    CollectionRun,
    ExportJob,
    Provider,
    SearchLeg,
    SearchQuery,
    Subscription,
    User,
    UserSession,
)
from app.services.export_files import export_file_name
from app.settings import Settings, get_settings

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_export_api_create_list_status_download_delete_and_owner_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS farescope_archive"))
            await connection.execute(text(price_observation_partition_ddl(date(1988, 4, 1))))
            await connection.execute(
                text(
                    "ALTER TABLE public.price_observations "
                    "DETACH PARTITION public.price_observations_y1988m04"
                )
            )
            await connection.execute(
                text("ALTER TABLE public.price_observations_y1988m04 SET SCHEMA farescope_archive")
            )
            await connection.execute(
                text("DROP TABLE farescope_archive.price_observations_y1988m04")
            )
            async with factory() as session, session.begin():
                owner, owner_session, owner_subscription = await _seed_api_owner(
                    session,
                    prefix="export-api-owner",
                )
                outsider, outsider_session, outsider_subscription = await _seed_api_owner(
                    session,
                    prefix="export-api-outsider",
                )
                provider_id = await session.scalar(
                    select(Provider.id).where(Provider.code == "ctrip")
                )
                assert provider_id is not None
                session.add(
                    CollectionRun(
                        search_query_id=owner_subscription.search_query_id,
                        provider_id=provider_id,
                        idempotency_key=f"purged-api-source-{uuid4().hex}",
                        status="succeeded",
                        attempt=1,
                        max_attempts=3,
                        scheduled_at=datetime(1988, 4, 15, tzinfo=UTC),
                        started_at=datetime(1988, 4, 15, tzinfo=UTC),
                        finished_at=datetime(1988, 4, 15, tzinfo=UTC),
                        run_metadata={"fixture": "purged-api-source"},
                    )
                )

            settings = Settings(export_directory=str(tmp_path))
            current_identity = {"value": CurrentIdentity(user=outsider, session=outsider_session)}

            async def identity_override() -> CurrentIdentity:
                return current_identity["value"]

            async def database_override() -> AsyncIterator[AsyncSession]:
                async with factory() as session:
                    yield session

            dispatched: list[str] = []
            monkeypatch.setattr(
                "app.api.routes.exports.enqueue_export_job",
                lambda job_id: dispatched.append(str(job_id)) or True,
            )
            app = create_app()
            app.dependency_overrides[get_current_identity] = identity_override
            app.dependency_overrides[get_database_session] = database_override
            app.dependency_overrides[get_settings] = lambda: settings
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={settings.csrf_cookie_name: "csrf-export-test"},
                headers={"x-csrf-token": "csrf-export-test"},
            ) as client:
                outsider_payload = _create_payload(outsider_subscription.id, "outsider-export")
                outsider_response = await client.post("/api/exports", json=outsider_payload)
                assert outsider_response.status_code == 202
                outsider_job_id = outsider_response.json()["id"]

                current_identity["value"] = CurrentIdentity(
                    user=owner,
                    session=owner_session,
                )
                payload = _create_payload(owner_subscription.id, "owner-export")
                created = await client.post("/api/exports", json=payload)
                assert created.status_code == 202
                created_body = created.json()
                job_id = created_body["id"]
                assert created_body["scope"] == "canonical_query"
                assert created_body["status"] == "pending"
                assert dispatched[-1] == job_id

                unavailable_payload = {
                    **_create_payload(owner_subscription.id, "purged-source-export"),
                    "rangeStart": "1988-04-01T00:00:00Z",
                    "rangeEnd": "1988-05-01T00:00:00Z",
                }
                unavailable = await client.post("/api/exports", json=unavailable_payload)
                assert unavailable.status_code == 422
                assert unavailable.json()["detail"] == (
                    "export source history is unavailable for UTC month(s): 1988-04; "
                    "choose a range within retained price history"
                )
                async with factory() as session:
                    assert (
                        await session.scalar(
                            select(ExportJob.id).where(
                                ExportJob.idempotency_key == "purged-source-export"
                            )
                        )
                    ) is None

                dispatch_count = len(dispatched)
                replay = await client.post("/api/exports", json=payload)
                assert replay.status_code == 202
                assert replay.json()["id"] == job_id
                assert len(dispatched) == dispatch_count
                conflict_payload = {**payload, "format": "csv"}
                conflict = await client.post("/api/exports", json=conflict_payload)
                assert conflict.status_code == 409

                forbidden_create = await client.post(
                    "/api/exports",
                    json=_create_payload(outsider_subscription.id, "foreign-subscription"),
                )
                assert forbidden_create.status_code == 404
                listing = await client.get(
                    "/api/exports",
                    params={"subscriptionId": str(owner_subscription.id)},
                )
                assert listing.status_code == 200
                assert [item["id"] for item in listing.json()["items"]] == [job_id]
                assert outsider_job_id not in listing.text

                status_response = await client.get(f"/api/exports/{job_id}")
                assert status_response.status_code == 200
                foreign_status = await client.get(f"/api/exports/{outsider_job_id}")
                assert foreign_status.status_code == 404

                contents = b'{"schema_version":1,"observations":[]}'
                file_name = export_file_name(
                    UUID(job_id),
                    "json",
                    nonce=uuid4().hex,
                )
                file_path = tmp_path / file_name
                file_path.write_bytes(contents)
                async with factory() as session, session.begin():
                    stored = await session.get(ExportJob, UUID(job_id))
                    assert stored is not None
                    stored.status = "succeeded"
                    stored.completed_at = datetime.now(UTC)
                    stored.expires_at = datetime.now(UTC) + timedelta(hours=1)
                    stored.processed_rows = 0
                    stored.row_count = 0
                    stored.file_name = file_name
                    stored.content_type = "application/json; charset=utf-8"
                    stored.size_bytes = len(contents)
                    stored.checksum_sha256 = hashlib.sha256(contents).hexdigest()

                download = await client.get(f"/api/exports/{job_id}/download")
                assert download.status_code == 200
                assert download.content == contents
                assert download.headers["cache-control"] == "private, no-store"
                assert "attachment" in download.headers["content-disposition"]

                removed = await client.delete(f"/api/exports/{job_id}")
                assert removed.status_code == 204
                assert not file_path.exists()
                assert (await client.get(f"/api/exports/{job_id}")).status_code == 404
                assert (await client.delete(f"/api/exports/{outsider_job_id}")).status_code == 404
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _seed_api_owner(
    session: AsyncSession,
    *,
    prefix: str,
) -> tuple[User, UserSession, Subscription]:
    suffix = uuid4().hex
    user = User(
        username=f"{prefix}-{suffix}",
        normalized_username=f"{prefix}-{suffix}",
        display_name=prefix,
        role="member",
        status="active",
    )
    query = SearchQuery(
        provider="ctrip",
        query_hash=uuid4().hex + uuid4().hex,
        trip_type="one_way",
        adults=1,
        children=0,
        infants=0,
        cabin="economy",
        currency="CNY",
        direct_only=False,
        normalized_query={"fixture": "export-api"},
    )
    session.add_all((user, query))
    await session.flush()
    user_session = UserSession(
        user_id=user.id,
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    subscription = Subscription(
        user_id=user.id,
        search_query_id=query.id,
        name="Export API route",
        enabled=True,
        poll_interval_seconds=21_600,
        tags=[],
    )
    session.add_all(
        (
            user_session,
            subscription,
            SearchLeg(
                search_query_id=query.id,
                position=0,
                origin_code="SHA",
                destination_code="TYO",
                departure_date=date(2026, 9, 1),
            ),
        )
    )
    await session.flush()
    return user, user_session, subscription


def _create_payload(subscription_id, idempotency_key: str) -> dict[str, str]:
    return {
        "subscriptionId": str(subscription_id),
        "format": "json",
        "rangeStart": "2026-07-01T00:00:00Z",
        "rangeEnd": "2026-07-08T00:00:00Z",
        "idempotencyKey": idempotency_key,
    }

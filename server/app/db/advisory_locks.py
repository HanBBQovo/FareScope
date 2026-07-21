from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

OBSERVATION_PARTITION_LOCK_ID = 6_323_717_466_347_023_758
EXPORT_STORAGE_RESERVATION_LOCK_ID = 2_888_573_881_489_187_019
EXPORT_JOB_ADMISSION_LOCK_ID = 7_110_483_029_462_155_487


async def acquire_observation_partition_shared_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(:lock_id)"),
        {"lock_id": OBSERVATION_PARTITION_LOCK_ID},
    )


async def try_acquire_observation_partition_exclusive_lock(session: AsyncSession) -> bool:
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
        {"lock_id": OBSERVATION_PARTITION_LOCK_ID},
    )
    return bool(acquired)


async def acquire_export_storage_reservation_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": EXPORT_STORAGE_RESERVATION_LOCK_ID},
    )


async def acquire_export_job_admission_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": EXPORT_JOB_ADMISSION_LOCK_ID},
    )

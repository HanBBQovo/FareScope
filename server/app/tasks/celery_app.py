from celery import Celery

from app.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "farescope",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=(
        "app.tasks.collection",
        "app.tasks.notifications",
        "app.tasks.scheduler",
    ),
)
celery_app.conf.update(
    enable_utc=True,
    timezone="UTC",
    task_serializer="json",
    result_serializer="json",
    accept_content=("json",),
    task_default_queue="default",
    task_routes={
        "farescope.collection.run": {"queue": "collector"},
        "farescope.collection.scheduler_tick": {"queue": "default"},
        "farescope.collection.maintain_partitions": {"queue": "default"},
        "farescope.alerts.evaluate_pending": {"queue": "analysis"},
        "farescope.notifications.deliver_pending": {"queue": "notifications"},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=1,
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.2,
    },
    result_expires=86_400,
    broker_transport_options={
        "visibility_timeout": max(settings.collection_run_lease_seconds * 2, 3600),
    },
    beat_schedule={
        "collection-scheduler-tick": {
            "task": "farescope.collection.scheduler_tick",
            "schedule": settings.collection_scheduler_tick_seconds,
        },
        "collection-partition-maintenance": {
            "task": "farescope.collection.maintain_partitions",
            "schedule": settings.collection_partition_maintenance_seconds,
        },
        "alert-evaluation": {
            "task": "farescope.alerts.evaluate_pending",
            "schedule": 30,
        },
        "notification-delivery": {
            "task": "farescope.notifications.deliver_pending",
            "schedule": 15,
        },
    },
)

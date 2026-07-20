from celery import Celery

from app.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "farescope",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    enable_utc=True,
    timezone="UTC",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)

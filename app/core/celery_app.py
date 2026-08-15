from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "informefinca",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.reports.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Madrid",
    enable_utc=True,
    # A report walks half a dozen public services; a slow WMS should not kill the job.
    task_soft_time_limit=15 * 60,
    task_time_limit=20 * 60,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

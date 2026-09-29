from celery import Celery
from config import Config

celery = Celery(
    "newsjacking_core",
    broker=Config.REDIS_URL,
    backend=Config.REDIS_URL,
    include=["tasks"],
)

celery.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="newsjacking",
    task_default_exchange="newsjacking",
    task_default_routing_key="newsjacking",
)

celery.conf.beat_schedule = {
    "newsjacking-hourly-scan": {
        "task": "newsjacking.hourly_scan",
        "schedule": Config.NEWSJACKING_INTERVAL_SECONDS,
    },
    "newsletter-schedule-tick": {
        "task": "newsletter.schedule_tick",
        "schedule": Config.NEWSLETTER_SCHEDULE_TICK_SECONDS,
    }
}

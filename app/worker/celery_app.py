from celery import Celery

from app.config import settings

broker = settings.celery_broker_url or settings.redis_url or "memory://"

celery_app = Celery("eve", broker=broker, include=["app.worker.tasks"])
celery_app.conf.update(
    task_always_eager=settings.celery_eager,
    task_eager_propagates=True,
    task_acks_late=True,  # a crashed worker's task is redelivered; all tasks are idempotent
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
    timezone="UTC",
    beat_schedule={
        "expire-stale-payments": {"task": "app.worker.tasks.expire_stale_payments", "schedule": 60.0},
    },
)

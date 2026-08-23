from __future__ import annotations

from celery import Celery

from scholarmotion.config import get_settings

settings = get_settings()
celery_app = Celery("scholarmotion", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "scholarmotion.tasks.planning.*": {"queue": "planning"},
        "scholarmotion.tasks.generation.*": {"queue": "code_generation"},
        "scholarmotion.tasks.tts.*": {"queue": "tts"},
        "scholarmotion.tasks.render.*": {"queue": "render"},
        "scholarmotion.tasks.verification.*": {"queue": "verification"},
        "scholarmotion.tasks.assembly.*": {"queue": "assembly"},
        "scholarmotion.tasks.editing.*": {"queue": "feedback"},
    },
    task_soft_time_limit=settings.render_timeout_seconds + 30,
)
celery_app.autodiscover_tasks(["scholarmotion.tasks"])

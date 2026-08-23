from __future__ import annotations

from scholarmotion.schemas import SceneSpec
from scholarmotion.verification.aggregator import aggregate_reports
from scholarmotion.verification.pedagogy import verify_pedagogy

from .celery_app import celery_app


@celery_app.task(name="scholarmotion.tasks.verification.scene")
def verify_scene(spec: dict) -> dict:
    value = SceneSpec.model_validate(spec)
    return aggregate_reports(value.scene_id, {"pedagogy": verify_pedagogy(value)}).model_dump(
        mode="json"
    )

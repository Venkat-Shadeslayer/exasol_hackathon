from __future__ import annotations

from .celery_app import celery_app
from .runtime import pipeline


@celery_app.task(name="scholarmotion.tasks.render.scene")
def render_scene(code: str, scene_class: str, output_dir: str) -> dict:
    return pipeline().renderer.render(code, scene_class, output_dir).__dict__

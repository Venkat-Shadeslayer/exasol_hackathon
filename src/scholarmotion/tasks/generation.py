from __future__ import annotations

import asyncio

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.schemas import SceneSpec

from .celery_app import celery_app
from .runtime import pipeline


@celery_app.task(name="scholarmotion.tasks.generation.code")
def generate_code(spec: dict) -> dict:
    runtime = pipeline()
    return asyncio.run(generate_scene_code(runtime.llm, SceneSpec.model_validate(spec))).model_dump(
        mode="json"
    )

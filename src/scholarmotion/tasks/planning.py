from __future__ import annotations

import asyncio
from uuid import UUID

from scholarmotion.agents.learner_profiler import profile_request
from scholarmotion.api.service import run_generation
from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database

from .celery_app import celery_app


@celery_app.task(name="scholarmotion.tasks.planning.profile")
def profile(request: str, duration: float = 5, language: str = "English") -> dict:
    return profile_request(request, target_duration_minutes=duration, language=language).model_dump(
        mode="json"
    )


@celery_app.task(name="scholarmotion.tasks.planning.build_project")
def build_project(project_id: str) -> dict:
    async def run() -> dict:
        settings = get_settings()
        database = Database(settings.database_url)
        try:
            result = await run_generation(database, settings, UUID(project_id))
            return {
                "project_id": result.project_id,
                "video_path": result.video_path,
                "scene_count": len(result.scenes),
            }
        finally:
            await database.close()

    return asyncio.run(run())

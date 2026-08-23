from __future__ import annotations

import asyncio
from uuid import UUID

from scholarmotion.api.service import edit_range
from scholarmotion.config import get_settings
from scholarmotion.media.timeline import parse_edit_instruction
from scholarmotion.persistence.database import Database

from .celery_app import celery_app


@celery_app.task(name="scholarmotion.tasks.editing.parse")
def parse_edit(text: str) -> dict:
    return parse_edit_instruction(text).model_dump(mode="json")


@celery_app.task(name="scholarmotion.tasks.editing.edit_project_range")
def edit_project_range(project_id: str, start: float, end: float, instruction: str) -> dict:
    async def run() -> dict:
        settings = get_settings()
        database = Database(settings.database_url)
        try:
            scenes = await edit_range(database, settings, UUID(project_id), start, end, instruction)
            return {"project_id": project_id, "affected_scenes": scenes}
        finally:
            await database.close()

    return asyncio.run(run())

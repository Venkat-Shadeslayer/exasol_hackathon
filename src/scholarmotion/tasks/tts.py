from __future__ import annotations

import asyncio
from pathlib import Path

from .celery_app import celery_app
from .runtime import pipeline


@celery_app.task(name="scholarmotion.tasks.tts.synthesize")
def synthesize(text: str, output_path: str, language: str = "English") -> dict:
    result = asyncio.run(pipeline().tts.synthesize(text, Path(output_path), language=language))
    return result.model_dump(mode="json")

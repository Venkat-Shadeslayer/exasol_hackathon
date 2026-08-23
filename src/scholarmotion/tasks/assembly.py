from __future__ import annotations

from .celery_app import celery_app
from .runtime import pipeline


@celery_app.task(name="scholarmotion.tasks.assembly.video")
def assemble(paths: list[str], output_path: str, audio_paths: list[str] | None = None) -> dict:
    return pipeline().assembler.assemble(paths, output_path, audio_paths=audio_paths).__dict__

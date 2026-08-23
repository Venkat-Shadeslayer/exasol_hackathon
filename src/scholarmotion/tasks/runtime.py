from __future__ import annotations

from scholarmotion.config import get_settings
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_embedding_provider,
    create_llm_provider,
    create_tts_provider,
)
from scholarmotion.services import ScholarMotionPipeline


def pipeline() -> ScholarMotionPipeline:
    settings = get_settings()
    return ScholarMotionPipeline(
        llm=create_llm_provider(settings),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
        renderer=SandboxRenderer(
            settings.manim_binary, settings.render_timeout_seconds, mock_missing=False
        ),
        assembler=FFmpegAssembler(settings.ffmpeg_binary, mock_missing=False),
        max_concurrent_scenes=settings.max_concurrent_scenes,
    )

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path as _Path
from uuid import uuid4

from scholarmotion.config import get_settings
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_embedding_provider,
    create_llm_provider,
    create_tts_provider,
)
from scholarmotion.retrieval.hybrid_retrieval import HybridRetriever
from scholarmotion.retrieval.ncert_ingestion import ingest_ncert_directory
from scholarmotion.services import ScholarMotionPipeline

NCERT_CHAPTER_DIR = "data/ncert/ncert_phy_class12"
NCERT_CHAPTER_FILE = "leph106.pdf"


def load_real_ncert_chunks(directory: str, filename: str) -> list[dict]:
    """Real chunks parsed from the actual NCERT Class 12 Physics EMI chapter."""
    records = [
        record
        for record in ingest_ncert_directory(directory)
        if _Path(record["path"]).name == filename
    ]
    chunks = []
    for index, record in enumerate(records, 1):
        text = record["text"].strip()
        if len(text) < 20:
            continue
        chunks.append(
            {
                "id": f"ncert-{filename.replace('.pdf', '')}-{index:03d}",
                "document_kind": "ncert",
                "class_level": record.get("class_level") or 12,
                "subject": "physics",
                "content_type": record["content_type"],
                "text": text,
                "concept_tags": record.get("concept_tags", []),
            }
        )
    return chunks


class RateLimitedLLM:
    """Serializes calls to stay under a free-tier requests-per-minute quota."""

    def __init__(self, inner, *, max_per_minute: int = 12):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.capabilities = inner.capabilities
        self._max_per_minute = max_per_minute
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] > 60:
                    self._calls.popleft()
                if len(self._calls) < self._max_per_minute:
                    self._calls.append(now)
                    return
                await asyncio.sleep(max(0.1, 61 - (now - self._calls[0])))

    async def _with_retry(self, call, *args, **kwargs):
        delays = [2, 8, 20]
        for attempt, delay in enumerate([*delays, None]):
            await self._throttle()
            try:
                return await call(*args, **kwargs)
            except Exception as exc:
                status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                transient = status in (429, 500, 502, 503, 504) or status is None
                if delay is None or not transient:
                    raise
                print(f"  (transient error {exc!r}, retrying in {delay}s...)")
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    async def generate_structured(self, prompt, output_schema, *, temperature=None):
        return await self._with_retry(
            self.inner.generate_structured, prompt, output_schema, temperature=temperature
        )

    async def generate_text(self, prompt, *, temperature=None):
        return await self._with_retry(self.inner.generate_text, prompt, temperature=temperature)

    async def generate_code(self, prompt, *, temperature=None):
        return await self._with_retry(self.inner.generate_code, prompt, temperature=temperature)

    async def analyze_images(self, prompt, images, *, temperature=None):
        return await self._with_retry(
            self.inner.analyze_images, prompt, images, temperature=temperature
        )


REQUEST = (
    "I am a Class 12 student. I understand magnetic fields but do not understand the principle "
    "of electromagnetic induction, magnetic flux and Faraday's law and Lenz's law. Explain the "
    "principle of electromagnetic induction at NCERT Class 12 level, building up from magnetic "
    "flux through Faraday's law of induction to Lenz's law. Make a 3-minute video."
)


async def main() -> None:
    settings = get_settings()
    project_id = f"emi-{uuid4().hex[:8]}"
    print(f"LLM provider: {settings.main_llm_provider} / model: {settings.main_llm_model}")
    print(f"TTS provider: {settings.tts_provider}")
    print(f"project_id: {project_id}")

    ncert_chunks = load_real_ncert_chunks(NCERT_CHAPTER_DIR, NCERT_CHAPTER_FILE)
    print(f"Parsed {len(ncert_chunks)} real chunks from {NCERT_CHAPTER_FILE}")

    pipeline = ScholarMotionPipeline(
        llm=RateLimitedLLM(create_llm_provider(settings), max_per_minute=12),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
    )
    retrieved = await HybridRetriever(pipeline.embeddings).search(
        REQUEST, ncert_chunks, metadata={"class_level": 12}, limit=10
    )
    print(f"Retrieved {len(retrieved)} knowledge chunks for grounding.")
    for item in retrieved:
        print(f"  [{item.score:.3f}] {item.chunk['text'][:90]!r}")

    result = await pipeline.build(
        project_id,
        REQUEST,
        duration_minutes=3,
        retrieved_chunks=[{**item.chunk, "score": item.score} for item in retrieved],
    )

    print("\n=== BUILD COMPLETE ===")
    print(f"Scenes produced: {len(result.scenes)}")
    for scene in result.scenes:
        print(
            f"  {scene.spec.scene_id}: {scene.spec.title!r} "
            f"({scene.audio.duration_seconds:.1f}s audio, score={scene.report.score:.2f})"
        )
    if result.blocked_scenes:
        print(f"BLOCKED scenes (excluded from video): {len(result.blocked_scenes)}")
        for blocked in result.blocked_scenes:
            print(f"  {blocked['scene_id']}: {blocked['title']!r}")
    print(f"Final video: {result.video_path}")
    print(f"Timeline scenes: {len(result.timeline.scenes)}")
    print(f"Subtitles: {result.srt_path}")
    print(f"Events: {result.events}")


if __name__ == "__main__":
    asyncio.run(main())

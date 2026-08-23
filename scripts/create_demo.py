from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from scholarmotion.config import get_settings
from scholarmotion.editing import EditableProject, EditableScene, SelectiveEditor
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_embedding_provider,
    create_llm_provider,
    create_tts_provider,
)
from scholarmotion.retrieval.hybrid_retrieval import HybridRetriever
from scholarmotion.services import ScholarMotionPipeline


async def main() -> None:
    settings = get_settings()
    project_id = f"demo-{uuid4().hex[:8]}"
    pipeline = ScholarMotionPipeline(
        llm=create_llm_provider(settings),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
    )
    request = "I am a Class 12 student. I understand magnetic fields but do not understand magnetic flux. Explain electromagnetic induction using NCERT-level mathematics. Make a 5-minute video."
    demo_ncert = [
        {
            "id": "ncert-demo-physics-flux",
            "document_kind": "ncert",
            "class_level": 12,
            "subject": "physics",
            "content_type": "definition",
            "text": "Magnetic flux through a surface is the surface integral of the magnetic field.",
            "concept_tags": ["magnetic flux", "magnetic field"],
        }
    ]
    retrieved = await HybridRetriever(pipeline.embeddings).search(
        request, demo_ncert, metadata={"class_level": 12}
    )
    result = await pipeline.build(
        project_id,
        request,
        duration_minutes=5,
        retrieved_chunks=[{**item.chunk, "score": item.score} for item in retrieved],
    )
    print(f"Created {len(result.scenes)} scenes: {result.video_path}")
    editable = EditableProject(
        project_id,
        [
            EditableScene(
                item.spec.scene_id,
                item.audio.duration_seconds,
                narration=item.spec.narration,
                render_bytes=Path(item.render_path).read_bytes(),
            )
            for item in result.scenes
        ],
    )
    editor = SelectiveEditor()
    editable.rebuild_timeline()
    changed = editor.edit_range(editable, 100, 115, "replace the example with a simpler one")
    print(f"Selective edit changed only: {changed}")
    defects = editor.edit_range(editable, 142, 146, "The equation overlaps the graph.")
    print(f"Visual defect repair candidates: {defects}")


if __name__ == "__main__":
    asyncio.run(main())

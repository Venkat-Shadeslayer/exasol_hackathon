"""Generate a narrated lesson on dipole-dipole interaction.

Mirrors the other demo scripts: builds the full artifact chain (profile →
curriculum → dossier → script → storyboard → SceneSpecs → code/TTS → render →
verification → assembly) for a Class 12 physics request, grounded in a small
NCERT-level corpus on electric dipoles.

    python scripts/create_dipole_demo.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from scholarmotion.config import get_settings
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_embedding_provider,
    create_llm_provider,
    create_tts_provider,
)
from scholarmotion.persistence.exasol import (
    ExasolConfig,
    ExasolUnavailable,
    bootstrap_async,
    load_corpus_async,
)
from scholarmotion.retrieval.exasol_retrieval import ExasolHybridRetriever
from scholarmotion.retrieval.hybrid_retrieval import HybridRetriever
from scholarmotion.services import ScholarMotionPipeline

REQUEST = (
    "I am a Class 12 student. I know what positive and negative charges are and "
    "how Coulomb's law works, but I do not understand what an electric dipole "
    "actually is. Explain the concept of an electric dipole from first "
    "principles: what it is physically, what the dipole moment means and why it "
    "is a vector, how its field differs from a single point charge, and what "
    "happens to a dipole placed in a uniform electric field. Use NCERT-level "
    "mathematics and build it up visually. Make a 5-minute video."
)

# NCERT-level grounding for the request. Kept inline so the demo is runnable
# without shipping copyrighted PDFs.
CORPUS = [
    {
        "id": "ncert-12-phy-dipole-moment",
        "document_kind": "ncert",
        "class_level": 12,
        "subject": "physics",
        "chapter": "Electric Charges and Fields",
        "content_type": "definition",
        "text": (
            "An electric dipole is a pair of equal and opposite charges q and -q "
            "separated by a small vector distance 2a. Its dipole moment is "
            "p = q(2a), directed from the negative charge to the positive charge."
        ),
        "concept_tags": ["electric dipole", "dipole moment", "charge"],
    },
    {
        "id": "ncert-12-phy-dipole-field",
        "document_kind": "ncert",
        "class_level": 12,
        "subject": "physics",
        "chapter": "Electric Charges and Fields",
        "content_type": "equation",
        "text": (
            "The field of a dipole falls off as 1/r^3, faster than the 1/r^2 of a "
            "point charge. On the axial line E = 2kp/r^3 and on the equatorial "
            "line E = -kp/r^3, where k = 1/(4*pi*epsilon_0)."
        ),
        "concept_tags": ["dipole field", "axial line", "equatorial line", "inverse cube"],
    },
    {
        "id": "ncert-12-phy-dipole-torque",
        "document_kind": "ncert",
        "class_level": 12,
        "subject": "physics",
        "chapter": "Electric Charges and Fields",
        "content_type": "equation",
        "text": (
            "A dipole in a uniform external field E experiences a torque "
            "tau = p x E and has potential energy U = -p . E = -pE cos(theta), "
            "where theta is the angle between the dipole moment and the field."
        ),
        "concept_tags": ["torque", "potential energy", "uniform field", "orientation"],
    },
    {
        "id": "ncert-12-phy-dipole-dipole",
        "document_kind": "ncert",
        "class_level": 12,
        "subject": "physics",
        "chapter": "Electric Charges and Fields",
        "content_type": "derivation",
        "text": (
            "When a second dipole sits in the non-uniform field of the first, the "
            "interaction energy of two dipoles p1 and p2 separated by r is "
            "U = k[(p1 . p2) - 3(p1 . r_hat)(p2 . r_hat)]/r^3. The energy therefore "
            "falls off as 1/r^3 and depends on the relative orientation of the two "
            "moments as well as on their separation."
        ),
        "concept_tags": [
            "dipole-dipole interaction",
            "interaction energy",
            "orientation",
            "separation",
        ],
    },
    {
        "id": "ncert-12-phy-dipole-orientation",
        "document_kind": "ncert",
        "class_level": 12,
        "subject": "physics",
        "chapter": "Electric Charges and Fields",
        "content_type": "example",
        "text": (
            "Two dipoles aligned head-to-tail along the line joining them attract, "
            "because the interaction energy is negative for that arrangement. Two "
            "dipoles lying parallel side by side repel, since the energy is "
            "positive. This orientation dependence is why polar molecules line up "
            "head-to-tail in a liquid."
        ),
        "concept_tags": [
            "dipole-dipole interaction",
            "attraction",
            "repulsion",
            "polar molecules",
        ],
    },
]


async def _load_into_exasol(settings, pipeline) -> None:
    """Publish the demo corpus to Exasol so retrieval has something to score."""
    config = ExasolConfig.from_settings(settings)
    await bootstrap_async(config)
    embeddings = await pipeline.embeddings.embed([item["text"] for item in CORPUS])
    document_id = str(uuid5(NAMESPACE_URL, "scholarmotion://demo/dipole"))
    chunks = [
        {
            "chunk_id": str(uuid5(NAMESPACE_URL, f"scholarmotion://demo/dipole/{item['id']}")),
            "document_id": document_id,
            "class_level": item["class_level"],
            "subject": item["subject"],
            "book": "NCERT Class 12 Physics",
            "chapter": item["chapter"],
            "section": item["chapter"],
            "page": 1,
            "content_type": item["content_type"],
            "text": item["text"],
            "concept_tags": item["concept_tags"],
            "embedding": embedding,
        }
        for item, embedding in zip(CORPUS, embeddings)
    ]
    document = {
        "document_id": document_id,
        "project_id": None,
        "kind": "ncert",
        "title": "NCERT Class 12 Physics — Electric Charges and Fields",
        "uri": "ncert://class12/physics/electric-charges-and-fields",
        "authors": ["NCERT"],
    }
    loaded = await load_corpus_async(config, [document], chunks)
    print(f"Loaded {loaded['chunks']} chunks into Exasol schema {settings.exasol_schema}")


async def _retrieve(settings, pipeline):
    """Retrieve grounding chunks, preferring Exasol as the corpus platform."""
    if settings.exasol_enabled:
        try:
            await _load_into_exasol(settings, pipeline)
            return await ExasolHybridRetriever(
                pipeline.embeddings, ExasolConfig.from_settings(settings)
            ).search(REQUEST, class_level=12, subject="physics", limit=5)
        except ExasolUnavailable as error:
            print(f"WARNING: Exasol unavailable, using in-process retrieval: {error}")
    return await HybridRetriever(pipeline.embeddings).search(
        REQUEST, CORPUS, metadata={"class_level": 12}, limit=5
    )


async def main() -> None:
    settings = get_settings()
    project_id = f"dipole-{uuid4().hex[:8]}"
    pipeline = ScholarMotionPipeline(
        llm=create_llm_provider(settings),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
    )

    print(f"Project: {project_id}")
    print(f"LLM provider: {settings.main_llm_provider} ({settings.main_llm_model})")
    print(f"TTS provider: {settings.tts_provider}")

    retrieved = await _retrieve(settings, pipeline)
    print(f"\nRetrieved {len(retrieved)} grounding chunks:")
    for item in retrieved:
        print(f"  {item.score:+.4f}  {item.chunk['id']}  ({', '.join(item.reasons)})")

    result = await pipeline.build(
        project_id,
        REQUEST,
        duration_minutes=5,
        retrieved_chunks=[{**item.chunk, "score": item.score} for item in retrieved],
    )

    print(f"\nBuilt {len(result.scenes)} scenes")
    total = 0.0
    for index, produced in enumerate(result.scenes, 1):
        duration = produced.audio.duration_seconds
        total += duration
        print(
            f"  S{index:02d} {produced.spec.title[:48]:<48} "
            f"{duration:6.2f}s  {Path(produced.render_path).name}"
        )
    print(f"\nTotal duration: {total:.2f}s")
    print(f"Video: {result.video_path}")

    root = Path(settings.object_storage_root) / project_id
    print(f"Artifacts: {root.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

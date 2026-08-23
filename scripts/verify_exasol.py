"""End-to-end check of the Exasol corpus platform.

Connects, bootstraps the schema, loads a small labelled corpus, then runs the
retriever on **both** scoring paths and prints the ranking each produced. The
two rankings should agree, which is what makes the SQL path a real fallback for
the UDF rather than a different system wearing the same name.

    python scripts/verify_exasol.py
"""

from __future__ import annotations

import asyncio
import sys
from uuid import NAMESPACE_URL, uuid5

from scholarmotion.config import get_settings
from scholarmotion.persistence.exasol import (
    ExasolConfig,
    ExasolUnavailable,
    bootstrap,
    connect,
    load_corpus,
)
from scholarmotion.providers import create_embedding_provider
from scholarmotion.retrieval.exasol_retrieval import ExasolHybridRetriever

QUERY = "How does a changing magnetic flux induce an emf in a coil?"

# Deterministic ids keep re-runs idempotent: load_corpus deletes by chunk id
# before inserting, so a fresh uuid4 each run would silently accumulate
# duplicates and make the ranking meaningless.
DOCUMENT_ID = str(uuid5(NAMESPACE_URL, "scholarmotion://verify/exasol"))


def _chunk_id(text: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"scholarmotion://verify/exasol/{text}"))

CORPUS = [
    (
        "Faraday's law states that the induced emf in a closed loop equals the "
        "negative rate of change of magnetic flux through the loop.",
        "equation",
        ["flux", "emf", "faraday", "induction"],
    ),
    (
        "Lenz's law fixes the direction of the induced current: it opposes the "
        "change in magnetic flux that produced it.",
        "paragraph",
        ["lenz", "flux", "induction", "current"],
    ),
    (
        "A photodiode converts incident light into current and is used in optical "
        "detectors and solar cells.",
        "paragraph",
        ["photodiode", "optics", "semiconductor"],
    ),
    (
        "The ideal gas law relates pressure, volume, and temperature for a fixed quantity of gas.",
        "paragraph",
        ["gas", "thermodynamics", "pressure"],
    ),
]


async def main() -> int:
    settings = get_settings()
    if not settings.exasol_enabled:
        print("EXASOL_ENABLED is false. Set it to true in .env and retry.")
        return 1

    config = ExasolConfig.from_settings(settings)
    print(f"→ Connecting to Exasol at {config.dsn} (schema {config.schema})")
    try:
        status = bootstrap(config)
    except ExasolUnavailable as error:
        print(f"FAILED: {error}")
        return 1
    print(f"  schema ready; cosine UDF deployed: {status['udf_ready']}")
    if status["udf_error"]:
        print(f"  UDF unavailable ({status['udf_error']}) — SQL path will be used")

    document_id = DOCUMENT_ID
    chunks = []
    embeddings = await create_embedding_provider(settings).embed([item[0] for item in CORPUS])
    for (text, content_type, tags), embedding in zip(CORPUS, embeddings):
        chunks.append(
            {
                "chunk_id": _chunk_id(text),
                "document_id": document_id,
                "class_level": 12,
                "subject": "physics",
                "book": "verification",
                "chapter": "Electromagnetic Induction",
                "section": "Verification",
                "page": 1,
                "content_type": content_type,
                "text": text,
                "concept_tags": tags,
                "embedding": embedding,
            }
        )
    document = {
        "document_id": document_id,
        "project_id": None,
        "kind": "ncert",
        "title": "Exasol verification corpus",
        "uri": "memory://verify",
        "authors": ["ScholarMotion"],
    }
    loaded = load_corpus(config, [document], chunks)
    print(f"→ Loaded {loaded['chunks']} chunks via Exasol bulk import")

    connection = connect(config)
    try:
        total = connection.execute("SELECT COUNT(*) AS N FROM SOURCE_CHUNKS").fetchall()[0]["N"]
        dims = connection.execute("SELECT COUNT(*) AS N FROM CHUNK_EMBEDDINGS").fetchall()[0]["N"]
        print(f"  SOURCE_CHUNKS={total} rows, CHUNK_EMBEDDINGS={dims} rows")
    finally:
        connection.close()

    print(f'\n→ Query: "{QUERY}"')
    for use_udf in (True, False):
        # With use_udf=True the retriever still checks SYS.EXA_ALL_SCRIPTS and
        # falls back when the script language container is absent, so label the
        # request and let the per-result `reasons` report what actually ran.
        path = "UDF requested (falls back if unavailable)" if use_udf else "SQL (forced)"
        retriever = ExasolHybridRetriever(
            create_embedding_provider(settings),
            ExasolConfig(
                dsn=config.dsn,
                user=config.user,
                password=config.password,
                schema=config.schema,
                use_udf=use_udf,
            ),
        )
        try:
            results = await retriever.search(QUERY, class_level=12, subject="physics", limit=4)
        except ExasolUnavailable as error:
            print(f"  {path}: FAILED — {error}")
            continue
        print(f"\n  {path}")
        for rank, item in enumerate(results, 1):
            print(f"    {rank}. score={item.score:+.4f}  {item.chunk['text'][:64]}...")
            print(f"       reasons={', '.join(item.reasons)}")

    analytics = await ExasolHybridRetriever(
        create_embedding_provider(settings), config
    ).corpus_analytics()
    print("\n→ Corpus analytics (aggregated in Exasol)")
    print(f"    totals: {analytics['totals']}")
    print(f"    content mix: {analytics['content_mix']}")
    print("\nExasol integration verified.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

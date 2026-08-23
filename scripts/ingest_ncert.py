from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.exasol import (
    ExasolConfig,
    ExasolUnavailable,
    bootstrap_async,
    load_corpus_async,
)
from scholarmotion.persistence.models import SourceChunk, SourceDocument
from scholarmotion.providers import create_embedding_provider
from scholarmotion.retrieval.ncert_ingestion import ingest_ncert_directory


async def run(directory: Path) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.create_all()
    records = ingest_ncert_directory(directory)
    embeddings = await create_embedding_provider(settings).embed([item["text"] for item in records])
    exasol_chunks: list[dict] = []
    async with database.sessions() as session:
        documents: dict[str, SourceDocument] = {}
        for record, embedding in zip(records, embeddings):
            path = record.pop("path")
            if path not in documents:
                document = SourceDocument(
                    kind="ncert",
                    title=Path(path).stem,
                    authors=["NCERT"],
                    uri=path,
                    metadata_json={"copyright": "Source PDF supplied by user; not redistributed."},
                )
                session.add(document)
                await session.flush()
                documents[path] = document
            allowed = {
                key: record.get(key)
                for key in (
                    "class_level",
                    "subject",
                    "book",
                    "chapter",
                    "section",
                    "page",
                    "content_type",
                    "text",
                    "equations",
                    "examples",
                    "definitions",
                    "concept_tags",
                    "prerequisite_tags",
                )
            }
            chunk = SourceChunk(document_id=documents[path].id, embedding=embedding, **allowed)
            session.add(chunk)
            await session.flush()
            exasol_chunks.append(
                {
                    **allowed,
                    "chunk_id": str(chunk.id),
                    "document_id": str(documents[path].id),
                    "embedding": embedding,
                }
            )
        exasol_documents = [
            {
                "document_id": str(document.id),
                "project_id": None,
                "kind": "ncert",
                "title": document.title,
                "uri": document.uri,
                "authors": ["NCERT"],
            }
            for document in documents.values()
        ]
        await session.commit()
    await database.close()
    print(f"Ingested {len(records)} chunks from {directory}")

    if settings.exasol_enabled:
        # The corpus is what Exasol serves at query time, so ingestion is not
        # complete until it lands there.
        try:
            await bootstrap_async(ExasolConfig.from_settings(settings))
            loaded = await load_corpus_async(
                ExasolConfig.from_settings(settings), exasol_documents, exasol_chunks
            )
            print(
                f"Loaded {loaded['chunks']} chunks / {loaded['documents']} documents "
                f"into Exasol schema {settings.exasol_schema}"
            )
        except ExasolUnavailable as error:
            print(f"WARNING: Exasol load skipped: {error}")
    else:
        print("EXASOL_ENABLED is false; corpus not mirrored to Exasol.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.directory))

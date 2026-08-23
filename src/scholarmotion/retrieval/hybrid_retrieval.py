from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: dict
    score: float
    reasons: tuple[str, ...]


class HybridRetriever:
    def __init__(self, embedding_provider):
        self.embedding_provider = embedding_provider

    async def search(
        self,
        query: str,
        chunks: list[dict],
        *,
        metadata: dict | None = None,
        expanded_concepts: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievedChunk]:
        filtered = [
            chunk
            for chunk in chunks
            if all(
                value is None or chunk.get(key) == value for key, value in (metadata or {}).items()
            )
        ]
        if not filtered:
            return []
        vectors = await self.embedding_provider.embed(
            [query, *[chunk.get("text", "") for chunk in filtered]]
        )
        query_vector, chunk_vectors = vectors[0], vectors[1:]
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        expanded = {item.lower() for item in (expanded_concepts or [])}
        results: list[RetrievedChunk] = []
        for chunk, vector in zip(filtered, chunk_vectors):
            text_terms = set(re.findall(r"[a-z0-9]+", chunk.get("text", "").lower()))
            lexical = len(query_terms & text_terms) / max(1, len(query_terms))
            cosine = sum(left * right for left, right in zip(query_vector, vector))
            tags = {item.lower() for item in chunk.get("concept_tags", [])}
            concept = len(expanded & tags) / max(1, len(expanded)) if expanded else 0
            phrase = 1 if query.lower() in chunk.get("text", "").lower() else 0
            score = 0.45 * cosine + 0.35 * lexical + 0.15 * concept + 0.05 * phrase
            reasons = tuple(
                name
                for name, value in (
                    ("vector", cosine),
                    ("full_text", lexical),
                    ("concept_graph", concept),
                    ("phrase", phrase),
                )
                if value > 0
            )
            results.append(RetrievedChunk(chunk, score, reasons))
        return sorted(
            results, key=lambda item: (item.score, len(item.chunk.get("text", ""))), reverse=True
        )[:limit]


class PostgresHybridRetriever:
    """Database-side pgvector + PostgreSQL FTS retrieval with metadata filters."""

    def __init__(self, embedding_provider):
        self.embedding_provider = embedding_provider

    async def search(
        self,
        session,
        query: str,
        *,
        project_id: str | None = None,
        class_level: int | None = None,
        subject: str | None = None,
        expanded_concepts: list[str] | None = None,
        limit: int = 12,
    ) -> list[RetrievedChunk]:
        vector = (await self.embedding_provider.embed([query]))[0]
        expanded_query = " ".join([query, *(expanded_concepts or [])])
        statement = text(
            """
            SELECT c.id, c.document_id, d.kind AS document_kind, c.class_level,
                   c.subject, c.chapter, c.section, c.page, c.content_type, c.text,
                   c.concept_tags,
                   (0.55 * (1 - (c.embedding <=> CAST(:embedding AS vector))) +
                    0.40 * ts_rank_cd(to_tsvector('english', c.text),
                                      plainto_tsquery('english', :query)) +
                    0.05 * CASE WHEN lower(c.text) LIKE :phrase THEN 1 ELSE 0 END) AS score
            FROM source_chunks c
            JOIN source_documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL
              AND (:project_id IS NULL OR d.project_id IS NULL OR d.project_id = CAST(:project_id AS uuid))
              AND (:class_level IS NULL OR c.class_level = :class_level)
              AND (:subject IS NULL OR lower(c.subject) = lower(:subject))
            ORDER BY score DESC
            LIMIT :limit
            """
        )
        rows = (
            await session.execute(
                statement,
                {
                    "embedding": "[" + ",".join(str(value) for value in vector) + "]",
                    "query": expanded_query,
                    "phrase": f"%{query.lower()}%",
                    "project_id": project_id,
                    "class_level": class_level,
                    "subject": subject,
                    "limit": limit,
                },
            )
        ).mappings()
        return [
            RetrievedChunk(dict(row), float(row["score"]), ("pgvector", "postgres_fts"))
            for row in rows
        ]

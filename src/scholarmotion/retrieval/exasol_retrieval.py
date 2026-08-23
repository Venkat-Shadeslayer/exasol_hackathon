"""Hybrid retrieval executed inside Exasol.

This is the third implementation of the retriever contract, alongside the
in-process ``HybridRetriever`` and ``PostgresHybridRetriever``. All three return
``RetrievedChunk`` so the pipeline is indifferent to which one ran.

Scoring keeps the same weighting the other retrievers use — vector similarity
dominates, lexical overlap corrects it, concept-graph overlap and exact phrase
presence break ties — but the whole blend is evaluated in the database. Nothing
but the final ranked page crosses the wire.

Two scoring paths exist:

``udf``
    ``COSINE_SIMILARITY`` runs as an Exasol Python UDF over the packed
    ``EMBEDDING_JSON`` column.
``sql``
    The dot product is a ``JOIN`` against the query vector plus a ``GROUP BY``
    ``SUM`` over the long ``CHUNK_EMBEDDINGS`` table — the columnar form.

The UDF is preferred and the SQL form is the fallback, so retrieval survives an
instance where script creation is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import re

from scholarmotion.persistence.exasol import ExasolConfig, connect
from scholarmotion.persistence.exasol import normalise as _normalise
from scholarmotion.persistence.exasol import udf_available

from .hybrid_retrieval import RetrievedChunk

# Mirrors HybridRetriever/PostgresHybridRetriever so results stay comparable
# across data platforms.
VECTOR_WEIGHT = 0.55
LEXICAL_WEIGHT = 0.30
CONCEPT_WEIGHT = 0.10
PHRASE_WEIGHT = 0.05

SELECTED_COLUMNS = """
    c.CHUNK_ID, c.DOCUMENT_ID, d.KIND AS DOCUMENT_KIND, c.CLASS_LEVEL, c.SUBJECT,
    c.CHAPTER, c."SECTION", c.PAGE, c.CONTENT_TYPE, c."TEXT", c.CONCEPT_TAGS
"""


class ExasolHybridRetriever:
    """Blended vector/lexical/concept retrieval pushed down into Exasol."""

    def __init__(self, embedding_provider, config: ExasolConfig):
        self.embedding_provider = embedding_provider
        self.config = config

    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        class_level: int | None = None,
        subject: str | None = None,
        expanded_concepts: list[str] | None = None,
        limit: int = 12,
    ) -> list[RetrievedChunk]:
        vector = (await self.embedding_provider.embed([query]))[0]
        return await asyncio.to_thread(
            self._search_sync,
            query,
            vector,
            project_id,
            class_level,
            subject,
            expanded_concepts or [],
            limit,
        )

    def _search_sync(
        self,
        query: str,
        vector: list[float],
        project_id: str | None,
        class_level: int | None,
        subject: str | None,
        expanded_concepts: list[str],
        limit: int,
    ) -> list[RetrievedChunk]:
        connection = connect(self.config)
        try:
            use_udf = self.config.use_udf and udf_available(connection, self.config.schema)
            filters, params = _filters(project_id, class_level, subject)
            lexical = _lexical_expression(query)
            concept = _concept_expression(expanded_concepts)
            params.update(
                {
                    "query_vector": json.dumps(list(vector)),
                    "phrase": f"%{query.lower()}%",
                    "limit": limit,
                }
            )
            if use_udf:
                params["query_vector"] = json.dumps(_normalise(vector))
                statement = _udf_statement(filters, lexical, concept)
                reasons = ("exasol_udf_cosine", "exasol_lexical", "exasol_concept")
            else:
                statement = _sql_statement(filters, lexical, concept, _query_values(vector))
                reasons = ("exasol_sql_dot_product", "exasol_lexical", "exasol_concept")
            rows = connection.execute(statement, params).fetchall()
            return [_to_chunk(row, reasons) for row in rows]
        finally:
            connection.close()

    async def corpus_analytics(self) -> dict:
        """Corpus coverage aggregations — the analytical half of the platform."""
        return await asyncio.to_thread(self._corpus_analytics_sync)

    def _corpus_analytics_sync(self) -> dict:
        connection = connect(self.config)
        try:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS CHUNKS,
                       COUNT(DISTINCT DOCUMENT_ID) AS DOCUMENTS,
                       COUNT(DISTINCT SUBJECT) AS SUBJECTS,
                       SUM(CASE WHEN EMBEDDING_JSON IS NULL THEN 1 ELSE 0 END) AS UNEMBEDDED
                FROM SOURCE_CHUNKS
                """
            ).fetchall()
            coverage = connection.execute(
                """
                SELECT CLASS_LEVEL, SUBJECT, CHAPTER,
                       COUNT(*) AS CHUNKS,
                       AVG(LENGTH("TEXT")) AS AVG_CHARS,
                       SUM(CASE WHEN CONTENT_TYPE = 'equation' THEN 1 ELSE 0 END) AS EQUATIONS
                FROM SOURCE_CHUNKS
                GROUP BY CLASS_LEVEL, SUBJECT, CHAPTER
                ORDER BY CHUNKS DESC
                LIMIT 50
                """
            ).fetchall()
            content_mix = connection.execute(
                """
                SELECT CONTENT_TYPE, COUNT(*) AS CHUNKS
                FROM SOURCE_CHUNKS
                GROUP BY CONTENT_TYPE
                ORDER BY CHUNKS DESC
                """
            ).fetchall()
            thin = connection.execute(
                """
                SELECT CLASS_LEVEL, SUBJECT, CHAPTER, COUNT(*) AS CHUNKS
                FROM SOURCE_CHUNKS
                WHERE CHAPTER IS NOT NULL
                GROUP BY CLASS_LEVEL, SUBJECT, CHAPTER
                HAVING COUNT(*) < 5
                ORDER BY CHUNKS ASC
                LIMIT 25
                """
            ).fetchall()
            row = totals[0] if totals else {}
            return {
                "platform": "exasol",
                "schema": self.config.schema,
                "scoring": "udf" if self.config.use_udf else "sql",
                "totals": {
                    "chunks": _int(row, "CHUNKS"),
                    "documents": _int(row, "DOCUMENTS"),
                    "subjects": _int(row, "SUBJECTS"),
                    "unembedded_chunks": _int(row, "UNEMBEDDED"),
                },
                "coverage": [
                    {
                        "class_level": _int(item, "CLASS_LEVEL"),
                        "subject": item.get("SUBJECT"),
                        "chapter": item.get("CHAPTER"),
                        "chunks": _int(item, "CHUNKS"),
                        "avg_chars": float(item.get("AVG_CHARS") or 0),
                        "equations": _int(item, "EQUATIONS"),
                    }
                    for item in coverage
                ],
                "content_mix": [
                    {"content_type": item.get("CONTENT_TYPE"), "chunks": _int(item, "CHUNKS")}
                    for item in content_mix
                ],
                "thin_coverage": [
                    {
                        "class_level": _int(item, "CLASS_LEVEL"),
                        "subject": item.get("SUBJECT"),
                        "chapter": item.get("CHAPTER"),
                        "chunks": _int(item, "CHUNKS"),
                    }
                    for item in thin
                ],
            }
        finally:
            connection.close()


def _int(row: dict, key: str) -> int | None:
    value = row.get(key)
    return None if value is None else int(value)


def _filters(
    project_id: str | None, class_level: int | None, subject: str | None
) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict = {}
    if project_id is not None:
        clauses.append("(d.PROJECT_ID IS NULL OR d.PROJECT_ID = {project_id})")
        params["project_id"] = project_id
    if class_level is not None:
        clauses.append("c.CLASS_LEVEL = {class_level!d}")
        params["class_level"] = class_level
    if subject is not None:
        clauses.append("LOWER(c.SUBJECT) = LOWER({subject})")
        params["subject"] = subject
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def _terms(query: str) -> list[str]:
    stop = {"the", "and", "with", "from", "that", "this", "into", "where", "when", "explain"}
    seen = [
        term
        for term in re.findall(r"[a-z0-9]{3,}", query.lower())
        if term not in stop and "'" not in term
    ]
    return list(dict.fromkeys(seen))[:12]


def _lexical_expression(query: str) -> str:
    """Fraction of query terms present, as an inline SQL expression.

    Exasol has no ``ts_rank_cd``; term presence over ``INSTR`` is the portable
    equivalent and stays a single columnar pass.
    """
    terms = _terms(query)
    if not terms:
        return "0"
    parts = [
        f"""CASE WHEN INSTR(LOWER(c."TEXT"), '{term}') > 0 THEN 1 ELSE 0 END""" for term in terms
    ]
    return f"(({' + '.join(parts)}) / {len(terms)}.0)"


def _concept_expression(expanded_concepts: list[str]) -> str:
    concepts = [item.lower() for item in expanded_concepts if item and "'" not in item][:12]
    if not concepts:
        return "0"
    parts = [
        f"CASE WHEN INSTR(LOWER(c.CONCEPT_TAGS), '\"{item}\"') > 0 THEN 1 ELSE 0 END"
        for item in concepts
    ]
    return f"(({' + '.join(parts)}) / {len(concepts)}.0)"


def _phrase_expression() -> str:
    return """CASE WHEN LOWER(c."TEXT") LIKE {phrase} THEN 1 ELSE 0 END"""


def _udf_statement(filters: str, lexical: str, concept: str) -> str:
    return f"""
        SELECT {SELECTED_COLUMNS},
               ({VECTOR_WEIGHT} * COSINE_SIMILARITY(c.EMBEDDING_JSON, {{query_vector}}) +
                {LEXICAL_WEIGHT} * {lexical} +
                {CONCEPT_WEIGHT} * {concept} +
                {PHRASE_WEIGHT} * {_phrase_expression()}) AS SCORE
        FROM SOURCE_CHUNKS c
        JOIN SOURCE_DOCUMENTS d ON d.DOCUMENT_ID = c.DOCUMENT_ID
        WHERE c.EMBEDDING_JSON IS NOT NULL{filters}
        ORDER BY SCORE DESC
        LIMIT {{limit!d}}
    """


def _sql_statement(filters: str, lexical: str, concept: str, query_values: str) -> str:
    """Dot product as a columnar join/aggregate against the query vector.

    ``VALUES`` materialises the query vector as a 384-row relation; joining it to
    the long ``CHUNK_EMBEDDINGS`` table turns similarity into one grouped SUM.
    Both sides are unit-normalised, so that sum is the cosine.

    ``query_values`` is inlined rather than bound: it is a generated list of
    numeric literals (see ``_query_values``), and Exasol cannot bind a whole
    relation to a single parameter. Every other value goes through pyexasol's
    formatter, which quotes strings and validates ``!d`` numerics.
    """
    return f"""
        WITH QUERY_VECTOR (DIM, "VALUE") AS (
            VALUES {query_values}
        ),
        SIMILARITY AS (
            SELECT e.CHUNK_ID, SUM(e."VALUE" * q."VALUE") AS COSINE
            FROM CHUNK_EMBEDDINGS e
            JOIN QUERY_VECTOR q ON q.DIM = e.DIM
            GROUP BY e.CHUNK_ID
        )
        SELECT {SELECTED_COLUMNS},
               ({VECTOR_WEIGHT} * s.COSINE +
                {LEXICAL_WEIGHT} * {lexical} +
                {CONCEPT_WEIGHT} * {concept} +
                {PHRASE_WEIGHT} * {_phrase_expression()}) AS SCORE
        FROM SOURCE_CHUNKS c
        JOIN SOURCE_DOCUMENTS d ON d.DOCUMENT_ID = c.DOCUMENT_ID
        JOIN SIMILARITY s ON s.CHUNK_ID = c.CHUNK_ID
        WHERE 1 = 1{filters}
        ORDER BY SCORE DESC
        LIMIT {{limit!d}}
    """


def _query_values(vector: list[float]) -> str:
    """Render the unit query vector as SQL numeric literals for a VALUES clause.

    Every element is coerced through ``float`` first, so nothing but a number
    can reach the statement text.
    """
    return ", ".join(
        f"({index}, {float(value):.9f})" for index, value in enumerate(_normalise(vector))
    )


def _to_chunk(row: dict, reasons: tuple[str, ...]) -> RetrievedChunk:
    row = {key.lower(): value for key, value in row.items()}
    tags = row.get("concept_tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    chunk = {
        "id": row.get("chunk_id"),
        "document_id": row.get("document_id"),
        "document_kind": row.get("document_kind"),
        "class_level": int(row["class_level"]) if row.get("class_level") is not None else None,
        "subject": row.get("subject"),
        "chapter": row.get("chapter"),
        "section": row.get("section"),
        "page": int(row["page"]) if row.get("page") is not None else None,
        "content_type": row.get("content_type"),
        "text": row.get("text") or "",
        "concept_tags": tags or [],
    }
    return RetrievedChunk(chunk, float(row.get("score") or 0.0), reasons)

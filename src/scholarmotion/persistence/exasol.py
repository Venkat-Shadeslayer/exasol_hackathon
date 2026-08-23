"""Exasol connectivity, schema bootstrap, and corpus loading.

Exasol is ScholarMotion's analytical data platform for the source corpus. The
corpus is the read-heavy, scan-heavy half of the system: every generation scores
the *entire* body of ingested NCERT/paper chunks against one query vector before
a single scene is planned. That is columnar MPP work, so it lives here rather
than in the OLTP store that tracks project/scene state.

Two representations of each embedding are maintained on purpose:

* ``SOURCE_CHUNKS.EMBEDDING_JSON`` — the packed vector, consumed by the
  ``COSINE_SIMILARITY`` Python UDF that runs inside Exasol.
* ``CHUNK_EMBEDDINGS`` — one row per (chunk, dimension). Long and narrow, which
  is the shape Exasol compresses and scans best, and it lets the dot product be
  expressed as a plain ``JOIN``/``SUM`` when the UDF is not deployed.

The retriever prefers the UDF and falls back to the relational form, so a demo
never dies on a failed script deployment.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import Any

from scholarmotion.config.settings import Settings

# Exasol has no array type; embeddings are packed as JSON text and additionally
# unrolled into CHUNK_EMBEDDINGS for the pure-SQL scoring path.
#
# SECTION, TEXT and VALUE are reserved words in Exasol (SYS.EXA_SQL_KEYWORDS),
# so they are double-quoted here and at every reference site.
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS SOURCE_DOCUMENTS (
        DOCUMENT_ID   VARCHAR(64),
        PROJECT_ID    VARCHAR(64),
        KIND          VARCHAR(30),
        TITLE         VARCHAR(500),
        URI           VARCHAR(2000),
        AUTHORS       VARCHAR(2000)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS SOURCE_CHUNKS (
        CHUNK_ID       VARCHAR(64),
        DOCUMENT_ID    VARCHAR(64),
        CLASS_LEVEL    DECIMAL(3,0),
        SUBJECT        VARCHAR(100),
        BOOK           VARCHAR(250),
        CHAPTER        VARCHAR(250),
        "SECTION"      VARCHAR(250),
        PAGE           DECIMAL(6,0),
        CONTENT_TYPE   VARCHAR(40),
        "TEXT"         VARCHAR(2000000),
        CONCEPT_TAGS   VARCHAR(20000),
        EMBEDDING_JSON VARCHAR(2000000)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS CHUNK_EMBEDDINGS (
        CHUNK_ID  VARCHAR(64),
        DIM       DECIMAL(6,0),
        "VALUE"   DOUBLE
    )
    """,
)

# Runs inside Exasol on the data node — no vectors cross the wire.
COSINE_UDF = """
CREATE OR REPLACE PYTHON3 SCALAR SCRIPT
COSINE_SIMILARITY(doc_vector VARCHAR(2000000), query_vector VARCHAR(2000000))
RETURNS DOUBLE AS
import json


def run(ctx):
    if ctx.doc_vector is None or ctx.query_vector is None:
        return 0.0
    left = json.loads(ctx.doc_vector)
    right = json.loads(ctx.query_vector)
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[i] * right[i] for i in range(size))
    left_norm = sum(value * value for value in left[:size]) ** 0.5
    right_norm = sum(value * value for value in right[:size]) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
/
"""


class ExasolUnavailable(RuntimeError):
    """Raised when Exasol is enabled but cannot be reached or driven."""


@dataclass(frozen=True)
class ExasolConfig:
    dsn: str
    user: str
    password: str
    schema: str
    use_udf: bool
    verify_tls: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> ExasolConfig:
        return cls(
            dsn=settings.exasol_dsn,
            user=settings.exasol_user,
            password=settings.exasol_password,
            schema=settings.exasol_schema,
            use_udf=settings.exasol_use_udf,
            verify_tls=settings.exasol_verify_tls,
        )


def connect(config: ExasolConfig):
    """Open a pyexasol connection with the working schema created and selected."""
    try:
        import pyexasol
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise ExasolUnavailable(
            "pyexasol is not installed; run: pip install -e '.[exasol]'"
        ) from error
    options: dict[str, Any] = {
        "dsn": config.dsn,
        "user": config.user,
        "password": config.password,
        "compression": True,
        "fetch_dict": True,
    }
    if not config.verify_tls:
        # pyexasol >= 1.0 defaults to strict certificate validation. A local
        # docker-db or an on-prem Personal instance serves a self-signed
        # certificate, which that default rejects outright. Opt out explicitly
        # rather than silently, and keep verification available for deployments
        # that terminate TLS with a real certificate.
        options["websocket_sslopt"] = {"cert_reqs": ssl.CERT_NONE}
    try:
        connection = pyexasol.connect(**options)
    except Exception as error:  # pragma: no cover - network dependent
        raise ExasolUnavailable(f"cannot reach Exasol at {config.dsn}: {error}") from error
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {config.schema}")
    connection.execute(f"OPEN SCHEMA {config.schema}")
    return connection


def bootstrap(config: ExasolConfig) -> dict[str, Any]:
    """Create the corpus tables and try to deploy the cosine-similarity UDF.

    UDF deployment is best-effort: a locked-down Exasol Personal instance may
    refuse script creation, and the SQL scoring path covers that case.
    """
    connection = connect(config)
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        udf_ready = False
        udf_error: str | None = None
        if config.use_udf:
            try:
                connection.execute(COSINE_UDF)
                udf_ready = True
            except Exception as error:  # pragma: no cover - permission dependent
                udf_error = str(error)
        return {"schema": config.schema, "udf_ready": udf_ready, "udf_error": udf_error}
    finally:
        connection.close()


def udf_available(connection, schema: str) -> bool:
    """Check the Exasol system catalog for a deployed COSINE_SIMILARITY script."""
    try:
        rows = connection.execute(
            "SELECT SCRIPT_NAME FROM SYS.EXA_ALL_SCRIPTS "
            "WHERE SCRIPT_SCHEMA = {schema} AND SCRIPT_NAME = 'COSINE_SIMILARITY'",
            {"schema": schema.upper()},
        ).fetchall()
        return bool(rows)
    except Exception:  # pragma: no cover - catalog access varies by edition
        return False


def normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so a dot product *is* the cosine.

    The pure-SQL scoring path sums ``VALUE * VALUE`` across a join and has no
    cheap way to divide by both norms afterwards, so normalisation happens once
    at ingest (and once per query) instead of on every scan.
    """
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0.0:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


def load_corpus(config: ExasolConfig, documents: list[dict], chunks: list[dict]) -> dict[str, int]:
    """Bulk-load documents and chunks using Exasol's native CSV import path.

    ``import_from_iterable`` streams rows over Exasol's parallel HTTP import
    rather than issuing per-row INSERTs, which is what makes ingesting a full
    NCERT volume practical.
    """
    connection = connect(config)
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        chunk_ids = [chunk["chunk_id"] for chunk in chunks]
        if chunk_ids:
            _delete_existing(connection, chunk_ids)
        # Documents need the same treatment: re-loading one without clearing the
        # old row leaves two rows sharing a DOCUMENT_ID, and the retrieval join
        # then returns every chunk once per duplicate.
        document_ids = [document["document_id"] for document in documents]
        if document_ids:
            _delete_documents(connection, document_ids)
        if documents:
            connection.import_from_iterable(
                (
                    (
                        document["document_id"],
                        document.get("project_id"),
                        document.get("kind", "ncert"),
                        document.get("title"),
                        document.get("uri"),
                        json.dumps(document.get("authors", [])),
                    )
                    for document in documents
                ),
                "SOURCE_DOCUMENTS",
            )
        if chunks:
            # Normalise once here so both scoring paths agree: the UDF gets unit
            # vectors, and the SQL path's SUM(VALUE * VALUE) is then the cosine.
            unit = {chunk["chunk_id"]: normalise(chunk.get("embedding") or []) for chunk in chunks}
            connection.import_from_iterable(
                (
                    (
                        chunk["chunk_id"],
                        chunk["document_id"],
                        chunk.get("class_level"),
                        chunk.get("subject"),
                        chunk.get("book"),
                        chunk.get("chapter"),
                        chunk.get("section"),
                        chunk.get("page"),
                        chunk.get("content_type", "paragraph"),
                        chunk.get("text", ""),
                        json.dumps(chunk.get("concept_tags", [])),
                        json.dumps(unit[chunk["chunk_id"]]),
                    )
                    for chunk in chunks
                ),
                "SOURCE_CHUNKS",
            )
            connection.import_from_iterable(
                (
                    (chunk_id, index, value)
                    for chunk_id, vector in unit.items()
                    for index, value in enumerate(vector)
                ),
                "CHUNK_EMBEDDINGS",
            )
        connection.commit()
        return {"documents": len(documents), "chunks": len(chunks)}
    finally:
        connection.close()


def _delete_documents(connection, document_ids: list[str]) -> None:
    """Clear documents by id so a re-load replaces rather than duplicates."""
    for batch in (document_ids[i : i + 500] for i in range(0, len(document_ids), 500)):
        values = ", ".join("'" + item.replace("'", "''") + "'" for item in batch)
        connection.execute(f"DELETE FROM SOURCE_DOCUMENTS WHERE DOCUMENT_ID IN ({values})")


def _delete_existing(connection, chunk_ids: list[str]) -> None:
    """Make re-ingestion idempotent; Exasol has no upsert for this shape."""
    for batch in (chunk_ids[index : index + 500] for index in range(0, len(chunk_ids), 500)):
        values = ", ".join("'" + item.replace("'", "''") + "'" for item in batch)
        connection.execute(f"DELETE FROM SOURCE_CHUNKS WHERE CHUNK_ID IN ({values})")
        connection.execute(f"DELETE FROM CHUNK_EMBEDDINGS WHERE CHUNK_ID IN ({values})")


async def load_corpus_async(
    config: ExasolConfig, documents: list[dict], chunks: list[dict]
) -> dict[str, int]:
    return await asyncio.to_thread(load_corpus, config, documents, chunks)


async def bootstrap_async(config: ExasolConfig) -> dict[str, Any]:
    return await asyncio.to_thread(bootstrap, config)

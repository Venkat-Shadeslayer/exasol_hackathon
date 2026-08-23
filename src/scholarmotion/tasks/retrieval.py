from __future__ import annotations

import asyncio

from scholarmotion.providers.embeddings.local import LocalEmbeddingProvider
from scholarmotion.retrieval.hybrid_retrieval import HybridRetriever

from .celery_app import celery_app


@celery_app.task(name="scholarmotion.tasks.retrieval.hybrid")
def hybrid_search(query: str, chunks: list[dict], limit: int = 8) -> list[dict]:
    async def run() -> list[dict]:
        results = await HybridRetriever(LocalEmbeddingProvider()).search(query, chunks, limit=limit)
        return [
            {"chunk": item.chunk, "score": item.score, "reasons": item.reasons} for item in results
        ]

    return asyncio.run(run())

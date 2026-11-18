"""Qdrant VectorStore adapter — uses the process-wide shared client."""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.access import access_filter
from app.config import Settings
from app.infra.qdrant import get_qdrant_client
from app.observe import error, logger
from app.ports.embeddings import Embedder


class QdrantVectorStore:
    """Role-filtered Qdrant search with an injectable embedder."""

    def __init__(self, settings: Settings, embedder: Embedder, client: QdrantClient | None = None):
        self._settings = settings
        self._embedder = embedder
        self._client = client or get_qdrant_client(settings)

    @property
    def client(self) -> Any:
        return self._client

    @property
    def collection(self) -> str:
        return self._settings.QDRANT_COLLECTION

    def search(self, query: str, roles: list[str], limit: int = 8) -> list[dict]:
        try:
            return self._search(query, roles=roles, limit=limit)
        except Exception as e:
            error(f"❌ Qdrant Search Failed after retries: {e}")
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _search(self, query: str, roles: list[str], limit: int = 8) -> list[dict]:
        query_vector = self._embedder.embed_query(query)
        response = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=access_filter(roles),
            limit=limit,
            with_payload=True,
        )
        results = []
        for res in response.points:
            results.append(
                {
                    "content": res.payload.get("text", ""),
                    "source": res.payload.get("source", "Unknown"),
                    "page": res.payload.get("page"),
                    "score": res.score,
                }
            )
        return results

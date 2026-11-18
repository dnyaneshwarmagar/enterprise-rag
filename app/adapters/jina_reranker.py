"""Jina Reranker adapter — delegates to the existing ranking helpers."""

from __future__ import annotations

from app.services.retrieval import ranking_service


class JinaRerankerAdapter:
    """Reranker backed by the Jina Reranker API."""

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[str]:
        return ranking_service.rerank_documents(query, documents, top_n=top_n)

"""Reranker port."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    """Reorder retrieved documents by relevance to the query."""

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[str]:
        """Return the top_n documents, most relevant first."""

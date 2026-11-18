"""Embedding port."""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    """Text → vector embeddings (Jina today)."""

    def embed_query(self, query: str) -> list[float]:
        """Embed a single retrieval query."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed document/passage texts."""

    def dim(self) -> int:
        """Embedding dimensionality."""

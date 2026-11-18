"""Jina Embeddings adapter — delegates to the existing module helpers."""

from __future__ import annotations

from app.services.retrieval import embedding as embedding_mod


class JinaEmbedder:
    """Embedder backed by the Jina Embeddings API."""

    def embed_query(self, query: str) -> list[float]:
        return embedding_mod.embed_query(query)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return embedding_mod.embed_texts(texts)

    def dim(self) -> int:
        return embedding_mod.get_embedding_dim()

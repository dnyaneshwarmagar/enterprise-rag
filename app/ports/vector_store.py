"""Vector store port."""

from __future__ import annotations

from typing import Any, Protocol


class VectorStore(Protocol):
    """Semantic search + admin access to the underlying client when needed."""

    @property
    def client(self) -> Any:
        """Raw client for ingestion/admin paths that need Qdrant APIs."""

    @property
    def collection(self) -> str:
        """Active collection name."""

    def search(self, query: str, roles: list[str], limit: int = 8) -> list[dict]:
        """Role-filtered similarity search. Returns chunk dicts."""

"""Process-wide Qdrant client — one connection for retrieval and ingestion."""

from __future__ import annotations

from qdrant_client import QdrantClient

from app.config import Settings
from app.config import settings as default_settings

# Ingestion upserts need a longer timeout than search; one client covers both.
_DEFAULT_TIMEOUT_SECONDS = 120

_client: QdrantClient | None = None


def get_qdrant_client(settings: Settings | None = None) -> QdrantClient:
    """Return the shared Qdrant client (created once per process)."""
    global _client
    if _client is None:
        cfg = settings or default_settings
        _client = QdrantClient(
            url=cfg.QDRANT_URL,
            api_key=cfg.QDRANT_API_KEY,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    return _client


def reset_qdrant_client_for_tests() -> None:
    """Drop the singleton so the next get_qdrant_client() builds a fresh client."""
    global _client
    _client = None

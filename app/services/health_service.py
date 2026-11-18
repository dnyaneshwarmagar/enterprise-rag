"""Health/readiness application service."""

from __future__ import annotations

from app.ports.llm import LLMGateway
from app.ports.vector_store import VectorStore
from app.services.health.connection_checker import ConnectionResult, check_all_connections, log_connection_summary


class HealthService:
    """Runs connection probes using the same clients as the live app when available."""

    def __init__(
        self,
        llm: LLMGateway | None = None,
        vector_store: VectorStore | None = None,
    ):
        self._llm = llm
        self._vector_store = vector_store

    def check_all(self) -> dict[str, ConnectionResult]:
        qdrant = self._vector_store.client if self._vector_store is not None else None
        return check_all_connections(llm=self._llm, qdrant_client=qdrant)

    def log_summary(self, results: dict[str, ConnectionResult] | None = None) -> bool:
        return log_connection_summary(results if results is not None else self.check_all())

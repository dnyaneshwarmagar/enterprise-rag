"""FastAPI dependency providers."""

from __future__ import annotations

from fastapi import Request

from app.adapters.nemo_guardrails import NemoGuardrailsAdapter
from app.container import AppContainer
from app.services.document_service import DocumentService
from app.services.query_service import QueryService


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("AppContainer is not initialized. Did startup_event run?")
    return container


def get_query_service(request: Request) -> QueryService:
    """Return the process QueryService, or a test-friendly lazy instance."""
    service = getattr(request.app.state, "query_service", None)
    if service is None:
        service = QueryService(
            guardrails=NemoGuardrailsAdapter(),
            get_agent=lambda: request.app.state.rag_agent,
        )
        request.app.state.query_service = service
    return service


def get_document_service(request: Request) -> DocumentService:
    """Return the process DocumentService, or a lazy default for tests."""
    service = getattr(request.app.state, "document_service", None)
    if service is None:
        service = DocumentService()
        request.app.state.document_service = service
    return service

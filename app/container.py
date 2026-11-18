"""Composition root — build process-scoped dependencies once at startup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.adapters.jina_embeddings import JinaEmbedder
from app.adapters.jina_reranker import JinaRerankerAdapter
from app.adapters.litellm_gateway import LiteLLMGateway
from app.adapters.nemo_guardrails import NemoGuardrailsAdapter
from app.adapters.portkey_llm import PortkeyLLMGateway
from app.adapters.qdrant_store import QdrantVectorStore
from app.adapters.s3_storage import S3ObjectStorage
from app.config import Settings, settings as default_settings
from app.gateway import client as gateway_client
from app.gateway.client import get_gateway, reset_gateway_for_tests
from app.infra.qdrant import get_qdrant_client
from app.ports.embeddings import Embedder
from app.ports.guardrails import GuardrailsPort
from app.ports.llm import LLMGateway
from app.ports.reranker import Reranker
from app.ports.storage import ObjectStorage
from app.ports.vector_store import VectorStore
from app.services.health_service import HealthService


@dataclass
class AppContainer:
    """Process-scoped dependency graph."""

    settings: Settings
    llm: LLMGateway
    embedder: Embedder
    vector_store: VectorStore
    reranker: Reranker
    guardrails: GuardrailsPort
    storage: ObjectStorage
    health_service: HealthService
    rag_agent: Any = None
    query_service: Any = None
    document_service: Any = None
    _build_graph: Callable[..., Any] | None = field(default=None, repr=False)

    def build_agent(self, checkpointer=None):
        """Compile the LangGraph agent with injected node dependencies."""
        if self._build_graph is None:
            from app.agents.graph import build_graph

            self._build_graph = build_graph
        self.rag_agent = self._build_graph(
            checkpointer=checkpointer,
            llm=self.llm,
            vector_store=self.vector_store,
            reranker=self.reranker,
        )
        return self.rag_agent


def _make_gateway(cfg: Settings) -> LLMGateway:
    if cfg.use_litellm_gateway:
        return LiteLLMGateway(cfg)
    if not cfg.PORTKEY_API_KEY:
        raise RuntimeError("LLM_GATEWAY=portkey requires PORTKEY_API_KEY")
    return PortkeyLLMGateway(cfg)


def build_container(settings: Settings | None = None) -> AppContainer:
    """Create adapters from settings. Does not initialize NeMo or the graph."""
    cfg = settings or default_settings
    # One process-wide LLM gateway (same object as app.gateway.client façade).
    if settings is not None:
        reset_gateway_for_tests()
        gateway_client._gateway = _make_gateway(cfg)
    llm = get_gateway()
    embedder = JinaEmbedder()
    # One shared Qdrant client for VectorStore, retrieval shim, and ingestion.
    vector_store = QdrantVectorStore(cfg, embedder, client=get_qdrant_client(cfg))
    health_service = HealthService(llm=llm, vector_store=vector_store)
    return AppContainer(
        settings=cfg,
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        reranker=JinaRerankerAdapter(),
        guardrails=NemoGuardrailsAdapter(),
        storage=S3ObjectStorage(),
        health_service=health_service,
    )

"""LLM gateway port."""

from __future__ import annotations

from typing import Any, Protocol


class LLMGateway(Protocol):
    """OpenAI-compatible chat + LangChain model factory (Portkey today)."""

    def model_id(self, slug: str | None = None, model: str | None = None) -> str:
        """Provider model id used for chat completions."""

    def judge_model_id(self) -> str:
        """Model id for offline/eval judge calls."""

    def get_langchain_llm(self, feature: str = "rag") -> Any:
        """LangChain chat model for planner / NeMo."""

    @property
    def sync_client(self) -> Any:
        """Sync OpenAI-compatible client (responder)."""

    def get_async_client(self, feature: str = "rag") -> Any:
        """Async OpenAI-compatible client."""

    def get_async_judge_client(self) -> Any:
        """Async client for RAGAS judge calls."""

    def extract_cache_status(self, response: Any) -> str:
        """Best-effort Portkey cache hit/miss from a completion response."""

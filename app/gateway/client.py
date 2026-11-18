"""Compatibility façade over the configured LLM gateway (LiteLLM by default).

Call sites may keep importing ``portkey_client`` / ``get_langchain_llm``;
everything resolves to one process-wide gateway instance.
"""

from __future__ import annotations

from typing import Any

from app.adapters.litellm_gateway import LiteLLMGateway
from app.adapters.portkey_llm import PortkeyLLMGateway
from app.config import settings
from app.ports.llm import LLMGateway

_gateway: LLMGateway | None = None


def _build_gateway() -> LLMGateway:
    if settings.use_litellm_gateway:
        return LiteLLMGateway(settings)
    if not settings.PORTKEY_API_KEY:
        raise RuntimeError(
            "LLM_GATEWAY=portkey requires PORTKEY_API_KEY, or set LLM_GATEWAY=litellm."
        )
    return PortkeyLLMGateway(settings)


def get_gateway() -> LLMGateway:
    """Return the process-wide LLM gateway adapter."""
    global _gateway
    if _gateway is None:
        _gateway = _build_gateway()
    return _gateway


def reset_gateway_for_tests() -> None:
    """Drop the singleton so the next get_gateway() rebuilds from settings."""
    global _gateway
    _gateway = None


def portkey_model(slug: str | None = None, model: str | None = None) -> str:
    """Active chat model id / LiteLLM alias (legacy name kept for imports)."""
    return get_gateway().model_id(slug=slug, model=model)


def get_langchain_llm(feature: str = "rag"):
    """
    Returns a ChatOpenAI pointed at the active gateway (LiteLLM proxy by default).

    Why ChatOpenAI:
      Both Portkey and LiteLLM expose an OpenAI-compatible HTTP API. ChatOpenAI
      supports base_url + default_headers, so LangChain / NeMo keep the same shape.
    """
    return get_gateway().get_langchain_llm(feature=feature)


def get_async_openai_client(feature: str = "rag"):
    """Async OpenAI client routed through the active gateway."""
    return get_gateway().get_async_client(feature=feature)


def judge_portkey_model() -> str:
    """Judge model id / alias (legacy name kept for imports)."""
    return get_gateway().judge_model_id()


def get_async_judge_client():
    """Async OpenAI-compatible client for RAGAS via the active gateway."""
    return get_gateway().get_async_judge_client()


def extract_cache_status(response: Any) -> str:
    """Best-effort gateway cache hit/miss from a completion response."""
    return get_gateway().extract_cache_status(response)


class _GatewayClientProxy:
    """Lazy alias for ``get_gateway().sync_client`` so imports stay stable."""

    def __getattr__(self, name: str):
        return getattr(get_gateway().sync_client, name)


# Legacy export name — now the active gateway sync client (LiteLLM or Portkey).
portkey_client = _GatewayClientProxy()

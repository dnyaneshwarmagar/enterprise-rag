"""LiteLLM-proxy-backed LLMGateway adapter (Portkey replacement)."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI, OpenAI

from app.config import Settings


class LiteLLMGateway:
    """OpenAI-compatible clients pointed at a LiteLLM proxy.

    The proxy owns primary/fallback routing, retries, and optional cache —
    same responsibilities Portkey had for this app.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._sync_client: Any | None = None

    @property
    def _base_url(self) -> str:
        return (self._settings.LITELLM_BASE_URL or "http://127.0.0.1:4000").rstrip("/")

    @property
    def _api_key(self) -> str:
        return self._settings.LITELLM_MASTER_KEY or "sk-litellm-local"

    def model_id(self, slug: str | None = None, model: str | None = None) -> str:
        """Return the LiteLLM model alias (not a provider-native id)."""
        if slug or model:
            # Explicit override for rare call sites; default aliases below.
            return (model or slug or self._settings.LITELLM_MODEL).strip()
        return (self._settings.LITELLM_MODEL or "rag-primary").strip()

    def judge_model_id(self) -> str:
        """Judge alias when routing judge traffic through the proxy."""
        return (self._settings.LITELLM_FALLBACK_MODEL or "rag-fallback").strip()

    def _default_headers(self, feature: str = "rag") -> dict:
        return {
            "x-litellm-tags": f"feature={feature},app=enterprise-rag",
        }

    @property
    def sync_client(self) -> Any:
        if self._sync_client is None:
            self._sync_client = wrap_openai(
                OpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    default_headers=self._default_headers("rag"),
                ),
                chat_name="LiteLLM completion",
            )
        return self._sync_client

    def get_langchain_llm(self, feature: str = "rag") -> ChatOpenAI:
        return ChatOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self.model_id(),
            default_headers=self._default_headers(feature),
        )

    def get_async_client(self, feature: str = "rag") -> AsyncOpenAI:
        return wrap_openai(
            AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._default_headers(feature),
            ),
            chat_name="LiteLLM completion",
        )

    def get_async_judge_client(self) -> AsyncOpenAI:
        """Proxy-backed judge client (evals prefer JUDGE_GROQ_API_KEY first)."""
        return wrap_openai(
            AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._default_headers("ragas-judge"),
            ),
            chat_name="LiteLLM RAGAS judge",
        )

    def extract_cache_status(self, response: Any) -> str:
        """Best-effort cache hit detection from LiteLLM proxy responses."""
        for attr in ("_raw_response", "_response", "_http_response", "headers"):
            raw = getattr(response, attr, None)
            if raw is None:
                continue
            headers = getattr(raw, "headers", None)
            if headers is None:
                continue
            for key in (
                "x-litellm-cache-hit",
                "x-litellm-cache-status",
                "x-cache",
                "x-portkey-cache-status",
            ):
                value = headers.get(key, "")
                if not value:
                    continue
                text = str(value).strip().upper()
                if text in {"HIT", "TRUE", "1", "YES"}:
                    return "HIT"
                if text in {"MISS", "FALSE", "0", "NO"}:
                    return "MISS"
                if "HIT" in text:
                    return "HIT"
        hidden = getattr(response, "_hidden_params", None) or {}
        if isinstance(hidden, dict) and hidden.get("cache_hit") is True:
            return "HIT"
        return "MISS"

"""Portkey-backed LLMGateway adapter."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI, OpenAI
from portkey_ai import PORTKEY_GATEWAY_URL, createHeaders

from app.config import Settings


class PortkeyLLMGateway:
    """Builds OpenAI-compatible clients that route through Portkey."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._sync_client: Any | None = None

    def model_id(self, slug: str | None = None, model: str | None = None) -> str:
        name = (slug or self._settings.PORTKEY_PRIMARY_SLUG).strip().lstrip("@")
        model_id = (model or self._settings.PORTKEY_MODEL or "openai/gpt-oss-20b").strip().lstrip("@")
        return f"@{name}/{model_id}"

    def judge_model_id(self) -> str:
        return self.model_id(slug=self._settings.PORTKEY_FALLBACK_SLUG)

    def _make_headers(self, feature: str = "rag") -> dict:
        header_kwargs = {
            "api_key": self._settings.PORTKEY_API_KEY,
            "metadata": {
                "feature": feature,
                "_user": "rag-system",
                "environment": "production",
            },
        }
        if self._settings.PORTKEY_PRIMARY_CONFIG_ID:
            header_kwargs["config_id"] = self._settings.PORTKEY_PRIMARY_CONFIG_ID
        return createHeaders(**header_kwargs)

    @property
    def sync_client(self) -> Any:
        if self._sync_client is None:
            self._sync_client = wrap_openai(
                OpenAI(
                    api_key=self._settings.PORTKEY_API_KEY,
                    base_url=PORTKEY_GATEWAY_URL,
                    default_headers=self._make_headers(),
                ),
                chat_name="Groq completion",
            )
        return self._sync_client

    def get_langchain_llm(self, feature: str = "rag") -> ChatOpenAI:
        return ChatOpenAI(
            api_key=self._settings.PORTKEY_API_KEY,
            base_url=PORTKEY_GATEWAY_URL,
            model=self.model_id(),
            default_headers=self._make_headers(feature),
        )

    def get_async_client(self, feature: str = "rag") -> AsyncOpenAI:
        return wrap_openai(
            AsyncOpenAI(
                api_key=self._settings.PORTKEY_API_KEY,
                base_url=PORTKEY_GATEWAY_URL,
                default_headers=self._make_headers(feature),
            ),
            chat_name="Groq completion",
        )

    def get_async_judge_client(self) -> AsyncOpenAI:
        return wrap_openai(
            AsyncOpenAI(
                api_key=self._settings.PORTKEY_API_KEY,
                base_url=PORTKEY_GATEWAY_URL,
                default_headers=self._make_headers("ragas-judge"),
            ),
            chat_name="Groq RAGAS judge",
        )

    def extract_cache_status(self, response: Any) -> str:
        for attr in ("_raw_response", "_response", "_http_response", "headers"):
            raw = getattr(response, attr, None)
            if raw is not None:
                headers = getattr(raw, "headers", None)
                if headers is not None:
                    status = headers.get("x-portkey-cache-status", "")
                    if status:
                        return status.upper()
        return "MISS"

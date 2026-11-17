from langchain_openai import ChatOpenAI
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI, OpenAI
from portkey_ai import PORTKEY_GATEWAY_URL, createHeaders

from app.config import settings

# Portkey routing:
#   - Every call is authenticated with PORTKEY_API_KEY and sent to
#     `@{PORTKEY_PRIMARY_SLUG}/{PORTKEY_MODEL}` on Groq.
#   - PORTKEY_PRIMARY_CONFIG_ID is optional. When it is set, the saved config
#     adds fallback, retry, and cache. When it is empty, those rules are skipped
#     and the request goes only to the primary provider slug.


def portkey_model(slug: str | None = None, model: str | None = None) -> str:
    """Portkey model id. A leading @ on the slug or model is ignored."""
    name = (slug or settings.PORTKEY_PRIMARY_SLUG).strip().lstrip("@")
    model_id = (model or settings.PORTKEY_MODEL or "openai/gpt-oss-20b").strip().lstrip("@")
    return f"@{name}/{model_id}"


def _make_headers(feature: str = "rag") -> dict:
    """Build Portkey auth headers, attaching a saved config only when one is set."""
    header_kwargs = {
        "api_key": settings.PORTKEY_API_KEY,
        "metadata": {
            "feature": feature,
            "_user": "rag-system",
            "environment": "production",
        },
    }
    if settings.PORTKEY_PRIMARY_CONFIG_ID:
        header_kwargs["config_id"] = settings.PORTKEY_PRIMARY_CONFIG_ID
    return createHeaders(**header_kwargs)


# OpenAI-compatible client routed through Portkey.
# The OpenAI SDK is used because Portkey's gateway is OpenAI-compatible.
# config_id is added only when PORTKEY_PRIMARY_CONFIG_ID is set.
# wrap_openai records this non-LangChain client in LangSmith. The responder
# calls it directly, so without the wrapper the final Groq completion is missing
# from the trace.
portkey_client = wrap_openai(
    OpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        default_headers=_make_headers(),
    ),
    chat_name="Groq completion",
)


def get_langchain_llm(feature: str = "rag") -> ChatOpenAI:
    """
    Returns a Portkey-backed ChatOpenAI - a drop-in for LangChain nodes.

    Why ChatOpenAI:
      Portkey is a proxy. It exposes an OpenAI-compatible endpoint at PORTKEY_GATEWAY_URL.
      ChatOpenAI supports base_url (points at Portkey) and default_headers (passes Portkey
      auth + saved-config reference). The @slug/model-name format is Portkey-specific - the
      upstream provider's own client does not understand it. Portkey is just in the middle.
    """
    return ChatOpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        model=portkey_model(),
        default_headers=_make_headers(feature),
    )


def get_async_openai_client(feature: str = "rag") -> AsyncOpenAI:
    """
    Returns an async OpenAI client that routes through the Portkey gateway.
    Use this for non-LangChain async LLM calls (e.g. async FastAPI endpoints).
    """
    return wrap_openai(
        AsyncOpenAI(
            api_key=settings.PORTKEY_API_KEY,
            base_url=PORTKEY_GATEWAY_URL,
            default_headers=_make_headers(feature),
        ),
        chat_name="Groq completion",
    )


def judge_portkey_model() -> str:
    """Portkey model id for RAGAS judge calls — uses the fallback (Groq backup) slug."""
    return portkey_model(slug=settings.PORTKEY_FALLBACK_SLUG)


def get_async_judge_client() -> AsyncOpenAI:
    """
    Async OpenAI-compatible client for RAGAS metrics via Portkey fallback slug (Groq).
    Avoids spending OPENAI_API_KEY / JUDGE_OPENAI_API_KEY on eval runs.
    """
    return wrap_openai(
        AsyncOpenAI(
            api_key=settings.PORTKEY_API_KEY,
            base_url=PORTKEY_GATEWAY_URL,
            default_headers=_make_headers("ragas-judge"),
        ),
        chat_name="Groq RAGAS judge",
    )


def extract_cache_status(response) -> str:
    """
    Pull x-portkey-cache-status from the response.

    The OpenAI SDK does not expose raw headers on parsed responses, so cache
    hit/miss tracking is best-effort. We inspect common attribute paths and
    fall back to 'MISS'.
    """
    for attr in ("_raw_response", "_response", "_http_response", "headers"):
        raw = getattr(response, attr, None)
        if raw is not None:
            headers = getattr(raw, "headers", None)
            if headers is not None:
                status = headers.get("x-portkey-cache-status", "")
                if status:
                    return status.upper()
    return "MISS"

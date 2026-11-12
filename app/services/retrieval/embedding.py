import logging

import requests
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.observe import info, logger, span, warning

BATCH_SIZE = 64
_EMBEDDING_DIM = 1024
_JINA_EMBEDDING_URL = "https://api.jina.ai/v1/embeddings"
_JINA_MODEL = "jina-embeddings-v3"

_ready = False


# ── Model initialisation ───────────────────────────────────────────────────────


def _probe_jina_api() -> bool:
    """Verify the Jina Embeddings API is reachable with the configured key."""
    if not settings.JINA_API_KEY:
        info("JINA_API_KEY not set — embeddings are unavailable.")
        return False

    try:
        response = requests.post(
            _JINA_EMBEDDING_URL,
            headers={
                "Authorization": f"Bearer {settings.JINA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": _JINA_MODEL,
                "task": "retrieval.query",
                "normalized": True,
                "input": ["probe"],
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("data"):
            raise RuntimeError("Jina API returned empty data")
        info("Jina Embeddings API ready (jina-embeddings-v3, 1024-dim).")
        return True
    except Exception as e:
        warning(f"Jina Embeddings API probe failed: {e}.")
        return False


def _init():
    """Confirm the Jina API once per process. Called lazily on first use."""
    global _ready
    if _ready:
        return
    if not _probe_jina_api():
        raise RuntimeError("Jina Embeddings API is unavailable. Set JINA_API_KEY and check the service.")
    _ready = True


# ── Public helpers ─────────────────────────────────────────────────────────────


def get_embedding_dim() -> int:
    """Return the vector dimension for the active model."""
    _init()
    return _EMBEDDING_DIM


# ── Jina API embedding ─────────────────────────────────────────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _embed_jina_batch(texts: list[str], task: str) -> list[list[float]]:
    """Call the Jina Embeddings API for a single batch."""
    response = requests.post(
        _JINA_EMBEDDING_URL,
        headers={
            "Authorization": f"Bearer {settings.JINA_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": _JINA_MODEL,
            "task": task,
            "normalized": True,
            "input": texts,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()

    results = payload.get("data", [])
    # Sort by index because the API may not preserve order in rare cases
    results_sorted = sorted(results, key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in results_sorted]


def _embed_jina(texts: list[str], task: str) -> list[list[float]]:
    """Embed texts via the Jina API in batches with retry."""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        with span("Embed batch", start=i, size=len(batch), model=_JINA_MODEL) as record:
            embeddings = _embed_jina_batch(batch, task)
            all_embeddings.extend(embeddings)
            record.outputs(status="ok", vectors=len(embeddings))
    return all_embeddings


def _embed(texts: list[str], task: str) -> list[list[float]]:
    """Embed texts with the Jina API."""
    _init()
    return _embed_jina(texts, task)


# ── Public API (same signatures as before) ─────────────────────────────────────


def embed_query(query: str) -> list[float]:
    """Embed a single query."""
    return _embed([query], task="retrieval.query")[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of document texts."""
    return _embed(texts, task="retrieval.passage")

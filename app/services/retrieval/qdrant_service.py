import logging

from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.access import access_filter
from app.config import settings
from app.infra.qdrant import get_qdrant_client
from app.observe import error, logger
from app.services.retrieval.embedding import embed_query

class _SharedQdrantProxy:
    """Module alias that always forwards to the process-wide singleton.

    Tests may still ``patch("app.services.retrieval.qdrant_service.client")``.
    """

    def __getattr__(self, name):
        return getattr(get_qdrant_client(), name)


# Same process-wide client used by ingestion and QdrantVectorStore.
client = _SharedQdrantProxy()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _search_enterprise_knowledge(query: str, roles: list[str], limit: int = 8):
    """Internal search with retry logic. ``roles`` is applied inside Qdrant."""
    query_vector = embed_query(query)

    response = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_vector,
        query_filter=access_filter(roles),
        limit=limit,
        with_payload=True,
    )

    results = []
    for res in response.points:
        results.append(
            {
                "content": res.payload.get("text", ""),
                "source": res.payload.get("source", "Unknown"),
                "page": res.payload.get("page"),
                "score": res.score,
            }
        )

    return results


def search_enterprise_knowledge(query: str, roles: list[str], limit: int = 8):
    """
    Search the knowledge base and keep only chunks the caller's roles may read.
    The filter runs in Qdrant, so restricted text never reaches the model.
    """
    try:
        return _search_enterprise_knowledge(query, roles=roles, limit=limit)
    except Exception as e:
        error(f"❌ Qdrant Search Failed after retries: {e}")
        return []

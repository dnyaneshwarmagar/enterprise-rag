from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from app.agents.state import AgentState
from app.observe import info, span
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_documents

if TYPE_CHECKING:
    from app.ports.reranker import Reranker
    from app.ports.vector_store import VectorStore


def _with_citation(doc: dict) -> str:
    """Keep the filename and page attached after reranking, which returns text only."""
    page = doc.get("page")
    location = f" p.{page}" if page else ""
    return f"[{doc.get('source', 'Unknown')}{location}]\n{doc.get('content', '')}"


def make_retrieve_node(
    vector_store: VectorStore | None = None,
    reranker: Reranker | None = None,
) -> Callable[[AgentState], dict]:
    """Build a retriever node closed over vector store + reranker ports."""

    def retrieve_node(state: AgentState):
        """
        Performs vector search and semantic reranking for technical queries.
        """
        query = state["current_query"]

        with span("🔍 Knowledge Retrieval"):
            info(f"Searching Qdrant for: {query}")
            roles = list(state.get("access_roles") or [])
            if vector_store is not None:
                raw_results = vector_store.search(query, roles=roles, limit=15)
            else:
                raw_results = search_enterprise_knowledge(query, roles=roles, limit=15)
            info(f"Retrieved {len(raw_results)} candidates for roles {roles}")

            doc_contents = [_with_citation(doc) for doc in raw_results]

            with span("⚖️ Semantic Reranking"):
                if reranker is not None:
                    reranked_contents = reranker.rerank(query, doc_contents, top_n=5)
                else:
                    reranked_contents = rerank_documents(query, doc_contents, top_n=5)
                info("Reranking complete. Kept top 5 most relevant chunks.")

            formatted_docs = [f"CONTENT: {doc}" for doc in reranked_contents]

        return {
            "documents": formatted_docs,
            "status": "Found technical context.",
            "plan": state["plan"] + ["Context Retrieved"],
        }

    return retrieve_node


retrieve_node = make_retrieve_node()

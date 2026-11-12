from app.agents.state import AgentState
from app.observe import info, span
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_documents


def _with_citation(doc: dict) -> str:
    """Keep the filename and page attached after reranking, which returns text only."""
    page = doc.get("page")
    location = f" p.{page}" if page else ""
    return f"[{doc.get('source', 'Unknown')}{location}]\n{doc.get('content', '')}"


def retrieve_node(state: AgentState):
    """
    Performs vector search and semantic reranking for technical queries.
    """
    query = state["current_query"]

    # Standard Retrieval Logic
    with span("🔍 Knowledge Retrieval"):
        info(f"Searching Qdrant for: {query}")
        roles = list(state.get("access_roles") or [])
        raw_results = search_enterprise_knowledge(query, roles=roles, limit=15)
        info(f"Retrieved {len(raw_results)} candidates for roles {roles}")

        doc_contents = [_with_citation(doc) for doc in raw_results]

        with span("⚖️ Semantic Reranking"):
            reranked_contents = rerank_documents(query, doc_contents, top_n=5)
            info("Reranking complete. Kept top 5 most relevant chunks.")

        formatted_docs = [f"CONTENT: {doc}" for doc in reranked_contents]

    return {
        "documents": formatted_docs,
        "status": "Found technical context.",
        "plan": state["plan"] + ["Context Retrieved"],
    }

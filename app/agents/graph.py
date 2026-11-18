from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agents.nodes.planner import make_planner_node, planner_node
from app.agents.nodes.responder import generate_node, make_generate_node
from app.agents.nodes.retriever import make_retrieve_node, retrieve_node
from app.agents.state import AgentState
from app.config import settings
from app.observe import info, warning
from app.ports.llm import LLMGateway
from app.ports.reranker import Reranker
from app.ports.vector_store import VectorStore


def create_checkpointer() -> BaseCheckpointSaver:
    """
    Create a durable Postgres checkpointer for production.
    Falls back to in-memory MemorySaver only if Postgres is unreachable.

    Note: we run setup() through a single autocommit connection because
    LangGraph's migrations include CREATE INDEX CONCURRENTLY, which Neon
    rejects when run inside a transaction (the default ConnectionPool mode).
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=settings.postgres_uri,
            max_size=20,
            open=False,
            timeout=10,
            num_workers=3,
            check=ConnectionPool.check_connection,
            max_idle=240,
        )
        # Verify connectivity before committing to Postgres; otherwise the first
        # graph invocation will hang on connection retries.
        pool.open()
        conn = pool.getconn()
        pool.putconn(conn)

        # Run migrations on a separate autocommit connection so that
        # CREATE INDEX CONCURRENTLY succeeds on Neon.
        try:
            with PostgresSaver.from_conn_string(settings.postgres_uri) as setup_saver:
                setup_saver.setup()
        except Exception as e:
            warning(f"⚠️ Postgres checkpointer setup failed ({e}); falling back to MemorySaver.")
            pool.close()
            return MemorySaver()

        checkpointer = PostgresSaver(pool)
        info("🗄️ Postgres checkpointer configured.")
        return checkpointer
    except Exception as e:
        warning(
            f"⚠️ Postgres checkpointer unavailable ({e}); falling back to MemorySaver. "
            "Do not use MemorySaver in production — state is lost on restart."
        )
        return MemorySaver()


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    llm: LLMGateway | None = None,
    vector_store: VectorStore | None = None,
    reranker: Reranker | None = None,
    planner: Any | None = None,
    retriever: Any | None = None,
    responder: Any | None = None,
) -> StateGraph:
    """
    Build and compile the LangGraph RAG agent.

    Args:
        checkpointer: Optional checkpointer. If None, a Postgres-backed
            checkpointer is created. Pass a MemorySaver in tests.
        llm / vector_store / reranker: When provided, node factories close over them.
        planner / retriever / responder: Optional pre-built node callables.
    """
    if checkpointer is None:
        checkpointer = create_checkpointer()

    if planner is None:
        planner = make_planner_node(llm.get_langchain_llm(feature="planner")) if llm is not None else planner_node
    if retriever is None:
        retriever = (
            make_retrieve_node(vector_store=vector_store, reranker=reranker)
            if vector_store is not None or reranker is not None
            else retrieve_node
        )
    if responder is None:
        responder = make_generate_node(llm) if llm is not None else generate_node

    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner)
    workflow.add_node("retriever", retriever)
    workflow.add_node("responder", responder)

    def route_planner(state: AgentState):
        if state["current_query"] == "CONVERSATIONAL":
            return "responder"
        return "retriever"

    workflow.set_entry_point("planner")
    workflow.add_conditional_edges("planner", route_planner, {"retriever": "retriever", "responder": "responder"})
    workflow.add_edge("retriever", "responder")
    workflow.add_edge("responder", END)

    return workflow.compile(checkpointer=checkpointer)


# Production code should call build_graph() explicitly (see app.main.startup_event).
# The old module-level `rag_agent` has been removed to avoid constructing two
# checkpointers and to make dependency injection/testability cleaner.

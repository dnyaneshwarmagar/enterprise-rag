import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langsmith import trace
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.access import apply_demo_role, resolve_roles, role_keys
from app.agents.graph import build_graph
from app.config import settings
from app.documents import (
    DuplicateDocument,
    index_upload,
    list_indexed_documents,
    remove_document,
    require_document_manager,
)
from app.guardrails import guard, initialize_rails
from app.health import router as health_router
from app.logging import set_request_id
from app.observe import error, info, warning
from app.services.health.connection_checker import check_all_connections, log_connection_summary

# Custom Prometheus metrics
RAG_REQUESTS_TOTAL = Counter(
    "rag_requests_total",
    "Total number of /query requests",
    ["status"],
)
RAG_REQUEST_DURATION = Histogram(
    "rag_request_duration_seconds",
    "Latency of /query requests in seconds",
)
GUARDRAILS_BLOCKS_TOTAL = Counter(
    "guardrails_blocks_total",
    "Number of requests blocked or allowed by guardrails",
    ["blocked"],
)

_security = HTTPBearer(auto_error=False)


def _init_rate_limiter():
    """Initialize rate limiting. Use Redis in production; fall back to in-memory storage locally."""
    from limits.storage import RedisStorage
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.extension import _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address

    try:
        storage = RedisStorage(settings.redis_url)
        # `storage.check()` returns False silently on some failures; ping the
        # underlying Redis client so we only use Redis when it is really reachable.
        if not storage.check() or not storage.storage.ping():
            raise ConnectionError("Redis did not respond to ping")
        app.state.limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)
        app.state.rate_limiter_storage = "redis"
        info("🚦 Rate limiting initialized via Redis.")
    except Exception as e:
        app.state.limiter = Limiter(key_func=get_remote_address)
        app.state.rate_limiter_storage = "memory"
        warning(f"⚠️ Redis unavailable ({e}); using in-memory rate limiting.")

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return True


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    """
    Resolve the caller's document roles from the bearer token.

    RAG_API_KEY and RAG_ROLE_KEYS are the only sources of roles. When neither
    is set, requests are accepted with DEFAULT_ACCESS_ROLES.
    """
    token = credentials.credentials if credentials else None
    roles = resolve_roles(token)
    if roles is None:
        warning("Unauthorized /query request: invalid or missing API key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return roles


def _get_limiter_rule(times: int, seconds: int) -> str:
    """Convert times/seconds into a slowapi limit string, e.g. '20/minute'."""
    if seconds % 60 == 0:
        return f"{times}/{seconds // 60}minute"
    if seconds % 3600 == 0:
        return f"{times}/{seconds // 3600}hour"
    return f"{times}/{seconds}second"


class _AppLimiter:
    """
    Thin wrapper around the Limiter instance that is initialized at startup.
    Allows routes to be decorated at import time while the real limiter
    (Redis-backed or in-memory) is configured in startup_event.
    """

    def limit(self, rule_or_callable):
        def decorator(func):
            import functools

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                limiter = getattr(app.state, "limiter", None)
                if limiter is None:
                    return func(*args, **kwargs)

                rule = rule_or_callable() if callable(rule_or_callable) else rule_or_callable
                # Build the slowapi wrapper at request time so the limiter
                # instance and storage backend are always current.
                return limiter.limit(rule)(func)(*args, **kwargs)

            return wrapper

        return decorator


app_limiter = _AppLimiter()


def rate_limit(times: int = None, seconds: int = None):
    """
    Decorator factory that applies slowapi rate limiting using the limiter
    initialized at startup. Falls back to a no-op if the limiter is missing.
    The rule is resolved at request time so settings can be overridden in tests.
    """

    def _resolve_rule() -> str:
        t = times or settings.RATE_LIMIT_PER_MINUTE
        s = seconds or 60
        return _get_limiter_rule(t, s)

    return app_limiter.limit(_resolve_rule)


# Initialize FastAPI
app = FastAPI(title="Enterprise Agentic RAG API")
app.include_router(health_router)

# Expose Prometheus metrics at /metrics with default request instrumentation.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.on_event("startup")
def startup_event():
    initialize_rails()

    # Build the agent graph with the production checkpointer (Postgres by default).
    app.state.rag_agent = build_graph()

    app.state.rate_limiter_enabled = _init_rate_limiter()

    # Verify all external dependencies are reachable.
    connection_results = check_all_connections()
    all_healthy = log_connection_summary(connection_results)
    if settings.STRICT_STARTUP and not all_healthy:
        failed = [name for name, r in connection_results.items() if not r.healthy]
        raise RuntimeError(f"STRICT_STARTUP enabled; failing services: {', '.join(failed)}")

    if not settings.API_KEY and not role_keys():
        warning(
            "RAG_API_KEY is not set — /query is open, limited to "
            f"roles: {settings.DEFAULT_ACCESS_ROLES}."
        )


class QueryRequest(BaseModel):
    q: str
    thread_id: Optional[str] = "default_user"
    demo_role: Optional[str] = None


class DeleteDocumentRequest(BaseModel):
    relative_path: str
    source_type: str = "general"
    demo_role: Optional[str] = None


def _manager_roles(
    token_roles: tuple[str, ...],
    demo_role: str | None,
    *,
    changes: bool = True,
) -> tuple[str, ...]:
    """Apply the open-demo role, then require a manager.

    ``changes`` is false for the document list, so a manager can see files
    while upload and delete stay turned off.
    """
    roles = apply_demo_role(token_roles, demo_role)
    require_document_manager(roles, changes=changes)
    return roles


@app.get("/")
def home():
    return {"message": "Enterprise LangGraph RAG API is live."}


@app.get("/graph")
def get_graph_image(_api_key: str = Depends(verify_api_key)):
    """
    Returns the Mermaid image of the agent's workflow.
    """
    try:
        png_bytes = app.state.rag_agent.get_graph().draw_mermaid_png()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return {"error": f"Could not generate graph image: {e}"}


@app.get("/documents")
def list_documents(
    demo_role: Optional[str] = None,
    roles: tuple[str, ...] = Depends(verify_api_key),
):
    """Documents a manager can review. Upload and delete stay off when the flag is false."""
    _manager_roles(roles, demo_role, changes=False)
    try:
        return {"documents": list_indexed_documents()}
    except Exception as exc:
        error(f"Document list failed: {exc}")
        raise HTTPException(status_code=500, detail="Could not list documents.") from exc


@app.post("/documents")
async def add_document(
    file: UploadFile = File(...),
    audience: str = Form(...),
    demo_role: Optional[str] = Form(None),
    roles: tuple[str, ...] = Depends(verify_api_key),
):
    """Index one file for the selected audience. Manager only, and only when enabled."""
    _manager_roles(roles, demo_role)
    content = await file.read()
    try:
        stored = index_upload(file.filename or "", content, audience)
    except DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error(f"Document upload failed: {exc}")
        raise HTTPException(status_code=500, detail="Could not add the document.") from exc
    return stored


@app.delete("/documents")
def delete_document(
    body: DeleteDocumentRequest,
    roles: tuple[str, ...] = Depends(verify_api_key),
):
    """Remove one indexed file. Manager only, and only when enabled."""
    _manager_roles(roles, body.demo_role)
    try:
        return remove_document(body.relative_path, body.source_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        error(f"Document delete failed: {exc}")
        raise HTTPException(status_code=500, detail="Could not delete the document.") from exc


@app.post("/query")
@rate_limit()
def query(
    request: Request,
    body: QueryRequest,
    roles: tuple[str, ...] = Depends(verify_api_key),
):
    """
    Runs the LangGraph RAG pipeline synchronously.
    Returns the final answer, thought process, status, and sources.
    """
    q = body.q
    thread_id = body.thread_id
    roles = apply_demo_role(roles, body.demo_role)
    request_id = str(uuid.uuid4())
    set_request_id(request_id)

    start = time.perf_counter()
    # Parent is set when the Streamlit UI forwards its LangSmith trace headers,
    # so the click and this request show up as one trace.
    parent = request.headers if request.headers.get("langsmith-trace") else None
    with trace(
        "rag_query",
        inputs={"question": q},
        metadata={"request_id": request_id, "thread_id": thread_id},
        parent=parent,
    ) as run:
        # Gate: run guardrails synchronously so blocked requests never run the graph.
        rail_fired, rail_response = guard(q)
        if rail_fired:
            GUARDRAILS_BLOCKS_TOTAL.labels(blocked="true").inc()
            RAG_REQUESTS_TOTAL.labels(status="blocked").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            info("🛡️ Request blocked by guardrails", request_id=request_id, thread_id=thread_id)
            blocked = {
                "question": q,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": [],
            }
            run.end(outputs=blocked)
            return blocked

        GUARDRAILS_BLOCKS_TOTAL.labels(blocked="false").inc()

        try:
            rag_agent = app.state.rag_agent
            initial_state = {
                "messages": [{"role": "user", "content": q}],
                "current_query": q,
                "documents": [],
                "plan": ["Start"],
                "status": "Initializing Graph...",
                "access_roles": list(roles),
            }
            config = {"configurable": {"thread_id": thread_id}}
            final_output = rag_agent.invoke(initial_state, config=config)

            RAG_REQUESTS_TOTAL.labels(status="success").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            info(
                "✅ RAG pipeline completed",
                request_id=request_id,
                thread_id=thread_id,
            )
            result = {
                "question": q,
                "answer": final_output.get("final_answer"),
                "thought_process": final_output.get("plan"),
                "status": final_output.get("status"),
                "sources": final_output.get("documents", []),
            }
            run.end(outputs={"answer": result["answer"], "status": result["status"]})
            return result
        except Exception as e:
            RAG_REQUESTS_TOTAL.labels(status="error").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            error(
                f"❌ RAG pipeline failed: {e}",
                request_id=request_id,
                thread_id=thread_id,
            )
            run.end(error=str(e))
            return JSONResponse(
                status_code=500,
                content={
                    "request_id": request_id,
                    "status": "error",
                    "message": "Failed to process request. Please try again later.",
                },
            )

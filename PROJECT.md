# Enterprise Agentic RAG — Project Documentation

End-to-end documentation for the **enterprise-rag** stack: request flow, technology choices, CI/CD, and tests.  
For dependency-injection details and Mermaid diagrams, see also [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 1. What this project is

An **enterprise agentic RAG** system over company / Kubernetes-style documents:

- Users ask questions in a **Streamlit chat UI**
- **FastAPI** runs **NeMo Guardrails**, then a **LangGraph** agent (planner → optional retrieval → responder)
- Retrieval uses **Qdrant** + **Jina** embeddings/rerank
- LLMs are reached through a **LiteLLM** proxy (default) → **Groq**, with Portkey as an optional rollback
- A separate **evals** Streamlit app drives golden-dataset pipeline checks and **RAGAS** metrics
- Production runs on **AWS EC2** via **Docker Compose**, with images from **ECR** and deploys from **GitHub Actions**

Package name: `enterprise-rag` (`pyproject.toml`).

---

## 2. End-to-end request flow

```text
Browser (Streamlit UI)
    │  POST /query  (via nginx /api/  in Compose)
    ▼
FastAPI  (app/main.py)
    │  API key / role  ·  rate limit  ·  LangSmith trace
    ▼
QueryService.ask
    │
    ├─► NeMo Guardrails  ──blocked──►  return answer + "Guardrails Fired"
    │
    └─► LangGraph agent
            │
            ├─ Planner   → LLM (gateway)  ·  intent / route
            │     │
            │     ├─ conversational ──────────────────────┐
            │     └─ technical → Retriever                 │
            │                       │                      │
            │                       ├─ Qdrant search       │
            │                       └─ Jina rerank (top 5) │
            │                              │               │
            └──────────────────────────────┴───────────────┤
                                                           ▼
                                                      Responder → LLM (gateway)
                                                           │
                                                           ▼
                                              JSON: answer, thought_process,
                                                    status, sources
```

### Step-by-step

| Step | Component | What happens |
|------|-----------|--------------|
| 1 | `ui/app.py` | User submits a question; UI POSTs to `{BACKEND_URL}/query` |
| 2 | `app/main.py` | Auth (`verify_api_key`), rate limit (Upstash Redis or in-memory), tracing |
| 3 | `QueryService` | Calls `GuardrailsPort.check` (`NemoGuardrailsAdapter`) |
| 4a | Guardrails block | Returns immediately; retrieval skipped |
| 4b | Guardrails pass | Compiles/runs LangGraph (`app/agents/graph.py`) |
| 5 | Planner | Classifies intent; technical queries go to retriever |
| 6 | Retriever | Qdrant search (role-filtered) → Jina rerank |
| 7 | Responder | Grounded answer via LLM gateway |
| 8 | Gateway | Default `LLM_GATEWAY=litellm` → OpenAI-compatible call to LiteLLM → Groq (`rag-primary` / `rag-fallback`) |

**Conversation memory:** LangGraph checkpointer prefers **Neon Postgres**; falls back to in-memory `MemorySaver`.

**Document ingestion (separate path):** loaders under `app/ingestion/` → `processed_data/` JSON → Jina embeddings → Qdrant.

---

## 3. Runtime topology (Docker Compose)

| Service | Role | Notes |
|---------|------|--------|
| `litellm` | LLM proxy | Image `enterprise-rag:litellm-main` (mirrored from GHCR); port `4000` |
| `api` | FastAPI RAG backend | Port `8000` (internal) |
| `ui` | Streamlit chat | Port `8501` |
| `evals` | Streamlit RAGAS / golden UI | Port `8502`, path `/evals/` |
| `nginx` | Reverse proxy | Host **`:80`** → `/` UI, `/api/` API, `/evals/` evals |

**Local:** `docker compose up --build -d`  
**EC2:** pre-built ECR images via `deploy/remote-update.sh` (`docker compose up -d --no-build`), repo at `/opt/enterprise-rag`.

Public health/query (on EC2 / local nginx):

- `GET  /api/health`
- `POST /api/query`

---

## 4. Technology stack

| Area | Choice |
|------|--------|
| Language | Python ≥ 3.11 (images use 3.12) |
| API | FastAPI + Uvicorn |
| UI | Streamlit |
| Agent orchestration | LangGraph + LangChain |
| Safety | NeMo Guardrails |
| Vector DB | Qdrant Cloud |
| Embeddings / rerank | Jina (`jina-embeddings-v3`, `jina-reranker-v3`) |
| LLM gateway | **LiteLLM** (default); Portkey optional |
| LLM provider | Groq (`openai/gpt-oss-20b` via LiteLLM aliases) |
| Checkpoints | Neon Postgres (`langgraph-checkpoint-postgres`) |
| Rate limiting | slowapi + Upstash Redis (in-memory fallback) |
| Object storage | Optional S3 (`boto3`) |
| Observability | **LangSmith** tracing + **Prometheus** (`/metrics`) |
| Evals | RAGAS (+ pandas); golden dataset in `evals/` |
| Packaging | Docker, Docker Compose, nginx |
| Cloud | AWS EC2, ECR, SSM, IAM OIDC from GitHub Actions |

Architecture style: **ports & adapters** — protocols in `app/ports/`, implementations in `app/adapters/`, wired once in `app/container.py`.

---

## 5. Repository map

| Path | Purpose |
|------|---------|
| `app/main.py` | FastAPI entry, `/query`, documents APIs |
| `app/container.py` | DI composition root |
| `app/services/` | `QueryService`, `DocumentService`, health |
| `app/agents/` | LangGraph graph + planner/retriever/responder nodes |
| `app/adapters/`, `app/ports/` | Hexagonal boundaries |
| `app/guardrails/` | NeMo / Colang rules |
| `app/ingestion/` | Document load/chunk/embed pipeline |
| `ui/app.py` | Chat UI |
| `evals/` | Golden dataset, live pipeline, RAGAS metrics, eval UI |
| `deploy/` | LiteLLM config/entrypoint, nginx, EC2 bootstrap/update, smoke |
| `tests/` | Pytest suite |
| `.github/workflows/` | CI + Deploy |
| `DATA/`, `processed_data/` | Source docs and parsed chunks |
| `access_policy.json` | Document access by role |
| `docker-compose.yml`, `Dockerfile`, `Dockerfile.evals` | Runtime images |

---

## 6. CI/CD

### 6.1 Continuous Integration — `.github/workflows/ci.yml`

**Triggers:** `pull_request`, and `workflow_call` (from Deploy).

**Job `test`:**

1. Checkout + Python **3.11**
2. `pip install -r requirements-prod.txt` (+ pytest, ruff)
3. **`ruff check app tests`**
4. **`pytest`** (dummy env vars; `STRICT_STARTUP=false`; no live Groq/Jina required)

### 6.2 Continuous Delivery — `.github/workflows/deploy.yml`

**Trigger:** push to **`main`**.

```text
test  →  publish  →  deploy  →  smoke
```

| Job | What it does |
|-----|----------------|
| **test** | Reuses `ci.yml` (lint + unit/integration tests) |
| **publish** | OIDC → IAM role `enterprise-rag-github`; login to ECR; mirror LiteLLM → `enterprise-rag:litellm-main`; build/push `enterprise-rag:main` and `enterprise-rag-evals:main` |
| **deploy** | SSM `AWS-RunShellScript` on EC2 `i-07a586aa1312f97c3` → `deploy/remote-update.sh` (git sync, ECR pull, Compose restart, inject Groq/LiteLLM env) |
| **smoke** | SSM localhost check: wait for `/api/health`, **one** live `POST /api/query`, assert non-empty `answer` |

**Important:** CD smoke is **health + single query only**. It does **not** run the golden dataset or RAGAS (Stage 2/3).

### 6.3 Images & registry

| Image | Tag(s) | Source |
|-------|--------|--------|
| `…/enterprise-rag` | `:main`, `:litellm-main` | App Dockerfile + LiteLLM mirror |
| `…/enterprise-rag-evals` | `:main` | `Dockerfile.evals` |

Registry: `815398003681.dkr.ecr.us-east-1.amazonaws.com` (private ECR).  
CD pushes **only `:main`** for app/evals (no per-commit SHA tags) so ECR storage stays smaller. Lifecycle policy expires **untagged** digests after 1 day.

### 6.4 Secrets & host config

**GitHub Actions secrets (CD injects onto EC2 `.env`):**

- `GROQ_API_KEY`, `GROQ_FALLBACK_API_KEY`, `JUDGE_GROQ_API_KEY`
- `LITELLM_MASTER_KEY`
- `RAG_API_KEY` (optional smoke auth)

**Host `.env` on EC2** (not baked into images) still needs Jina, Qdrant, Neon, Upstash, etc. for a healthy stack.

### 6.5 Local / manual smoke

```bash
SMOKE_BASE_URL=http://127.0.0.1:8000 bash deploy/smoke_groq_query.sh
# or via nginx: SMOKE_BASE_URL=http://<host>/api
```

---

## 7. Tests

CI runs **`ruff`** then **`pytest`** against `tests/` (`pyproject.toml` `testpaths = ["tests"]`).

| File | What it covers |
|------|----------------|
| `test_access.py` | Access policy roles, demo role, token → role resolution |
| `test_auth.py` | Open mode, missing/invalid/valid API keys, role keys |
| `test_async_query.py` | `/query` happy path + guardrails block behavior |
| `test_guardrails.py` | Label parsing, off-topic, jailbreak, injection-style cases |
| `test_health.py` | `/health`, `/ready` (200 / 503) |
| `test_checkpointer.py` | Postgres checkpointer vs `MemorySaver` |
| `test_connection_checker.py` | Neon / Redis / Qdrant / gateway / Jina health helpers |
| `test_documents.py` | Admin flag, upload/delete, audiences, duplicates |
| `test_ingestion.py` | Chunking, IDs, S3 URI, ingest targets |
| `test_retriever_access.py` | Roles passed into vector search |
| `test_retry.py` | Qdrant/rerank retry + fallback |
| `test_rate_limit.py` | Excessive requests blocked |
| `test_metrics.py` | Prometheus `/metrics` RAG counters |
| `test_evals.py` | Tool detection helpers, pipeline with mocks, guardrail metrics |

**Not in CI pytest:** live Groq smoke (CD / `deploy/smoke_groq_query.sh` only).

---

## 8. Evaluation suite (outside CD smoke)

| Asset | Detail |
|-------|--------|
| Golden dataset | `evals/golden_dataset.json` — **15** RAG samples, **9** guardrail samples |
| Stage 1 | Show / validate golden Q&A |
| Stage 2 | Live pipeline — call running `/query` for each sample (`evals/pipeline.py`) |
| Stage 3 | RAGAS metrics (`evals/metrics.py`) — Faithfulness, Answer Relevancy, Context Precision/Recall, Answer Correctness, Tool Correctness (Jaccard, no LLM) |
| UI | Streamlit `evals/app.py` at `/evals/` |
| Headless | `python -m evals.run_evals` → pipeline + guardrails report |
| **Metric report** | Full tables + component impact → [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md) |

**Judge LLM:** prefer `JUDGE_GROQ_API_KEY` (Groq OpenAI-compatible API); else LiteLLM fallback. Embeddings for RAGAS use Jina.

When Groq/OpenAI **rate limits / TPD are exhausted**, skip Stage 2/3 or run with `SKIP_PIPELINE=1` / sample limits — do not treat CD smoke failure as a substitute for full evals.

### 8.1 Latest metric snapshot (vs baseline)

| Area | Baseline / earlier | Current | Δ |
|------|--------------------|---------|---|
| Tool routing (15 golden RAG) | Early live: many fails / empty | Local **15/15** retrieve | Large ↑ |
| Guardrails (9 cases) | No rails → attacks unblocked | Acc **0.889**, P **1.0**, R **0.833** | Strong ↑ (1 FN left) |
| Answer Relevancy | **0.646** (old truncate) | **0.589–0.595** | No gain from prompt/truncate |
| Context Precision | Vector-only (unmeasured A/B) | **0.600** with Jina rerank | Fair; main retrieval lever |
| Faithfulness | — | **0.059\*** | \*Untrusted (context-format artifact); re-score pending |
| Context Recall / Answer Correctness | — | Incomplete (Groq limits) | Re-run later |

### 8.2 Component → metric impact

| Component | Config | Metrics it moves most |
|-----------|--------|------------------------|
| **Reranker** | Qdrant top **15** → Jina top **5** | **Context Precision** (primary); Recall/Faithfulness/Correctness indirect |
| **Guardrails** | NeMo before LangGraph | **Guardrail P/R/Acc**; keeps jailbreaks out of RAGAS answer scores |
| **Chunking** | **1500** chars, overlap **200** | **Context Recall / Precision** (fact splitting vs topic mix); Correctness if steps split |

Details, caveats, and next optimization priorities: [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md).

---

## 9. Configuration highlights

| Variable | Role |
|----------|------|
| `LLM_GATEWAY` | `litellm` (default) or `portkey` |
| `LITELLM_BASE_URL` | Proxy URL (`http://litellm:4000` in Compose) |
| `LITELLM_MODEL` / `LITELLM_FALLBACK_MODEL` | Aliases `rag-primary` / `rag-fallback` |
| `GROQ_API_KEY` / `GROQ_FALLBACK_API_KEY` | Consumed by LiteLLM config |
| `JUDGE_GROQ_API_KEY` | Direct RAGAS judge (optional fallback for LiteLLM key) |
| `JINA_API_KEY` | Embeddings + rerank |
| `QDRANT_*` | Vector DB |
| `NEON_DB_URL` | LangGraph checkpointer |
| `UPSTASH_REDIS_*` | Rate limiting |
| `RAG_API_KEY` / `RAG_ROLE_KEYS` | API auth & RBAC |
| `BACKEND_URL` | UI → API |

See `.env.example` for the full template. Secrets must never be committed.

LiteLLM entrypoint (`deploy/litellm_entrypoint.sh`): can fall back `GROQ_API_KEY` ← `JUDGE_GROQ_API_KEY`, and unsets Neon/Redis env so the proxy does not attach Prisma DB or Redis cache by mistake.

---

## 10. Typical developer workflows

### Run locally (Compose)

```bash
cp .env.example .env   # fill keys
docker compose up --build -d
# UI http://localhost/   API http://localhost/api/health   Evals http://localhost/evals/
```

### Run tests locally

```bash
pip install -r requirements-prod.txt
pip install pytest ruff
ruff check app tests
pytest
```

### Deploy

Push to `main` → Actions **Deploy** workflow (test → publish → EC2 update → single-query smoke).

Manual EC2 update path: SSM or SSH → `bash /opt/enterprise-rag/deploy/remote-update.sh` with image env vars set.

---

## 11. Operational notes

- **EC2 instance:** `t3.micro`-class Free Tier–eligible shape; keep a single always-on instance for Free Tier hours.
- **ECR private storage:** Free Tier is **500 MB/month** for private repos (first year). Kept images (`main` + `litellm-main` + evals `main`) are ~1.5 GB → small overage (~$0.10/mo at $0.10/GB). Public ECR’s **50 GB** free tier is **not** the same as private.
- **Smoke vs evals:** green CD smoke means health + one Groq-backed answer; it does **not** certify RAGAS quality.
- **ARCHITECTURE.md drift:** diagrams may still mention Logfire / “6 guardrail tests”; code uses **LangSmith + Prometheus**, and the golden file has **9** guardrail samples.

---

## 12. Quick reference links (in-repo)

| Topic | Location |
|-------|----------|
| Architecture / Mermaid | `ARCHITECTURE.md` |
| CI | `.github/workflows/ci.yml` |
| CD | `.github/workflows/deploy.yml` |
| Compose | `docker-compose.yml` |
| LiteLLM models | `deploy/litellm_config.yaml` |
| EC2 update | `deploy/remote-update.sh` |
| Smoke script | `deploy/smoke_groq_query.sh` |
| Golden data | `evals/golden_dataset.json` |
| Eval metric report | `evals/EVAL_REPORT.md` |
| Env template | `.env.example` |

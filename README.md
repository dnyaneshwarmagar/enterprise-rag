# Enterprise Agentic RAG

Production-oriented **agentic RAG** for enterprise IT docs (Kubernetes, jobs, ops).

Users ask questions in a chat UI. The API runs **NeMo Guardrails**, then a **LangGraph** agent (planner → retrieve → respond). Answers are grounded via **Qdrant** + **Jina**, with LLMs behind a **LiteLLM** proxy → **Groq**. The stack ships with Docker Compose, AWS EC2 deploy, and GitHub Actions CI/CD.

---

## Features

- **Agentic flow** — intent planning, role-filtered retrieval, grounded responses  
- **Safety** — NeMo Guardrails before the graph (jailbreak / off-topic blocking)  
- **LLM gateway** — LiteLLM with primary + fallback models (Portkey optional rollback)  
- **Clean architecture** — ports & adapters (`app/ports`, `app/adapters`, `app/container.py`)  
- **Observability** — LangSmith tracing + Prometheus `/metrics`  
- **Auth & limits** — API keys / RBAC roles, Upstash Redis rate limiting  
- **CI/CD** — lint + pytest → ECR publish → EC2 SSM deploy → live `/query` smoke  

---

## Architecture (request path)

```text
Streamlit UI
    → nginx (/api)
    → FastAPI  (auth · rate limit · trace)
    → NeMo Guardrails
         ├─ blocked → refusal
         └─ pass → LangGraph
                      ├─ Planner
                      ├─ Retriever (Qdrant → Jina rerank)
                      └─ Responder → LiteLLM → Groq
```

Deeper diagrams and DI notes: [`ARCHITECTURE.md`](./ARCHITECTURE.md) · full write-up: [`PROJECT.md`](./PROJECT.md)

---

## Tech stack

| Layer | Tech |
|-------|------|
| API / UI | FastAPI, Streamlit, Python 3.11+ |
| Agent | LangGraph, LangChain |
| Safety | NeMo Guardrails |
| Retrieval | Qdrant Cloud, Jina embeddings + reranker |
| LLM | LiteLLM proxy → Groq |
| Memory / limits | Neon Postgres, Upstash Redis |
| Runtime | Docker Compose, nginx |
| Cloud / CD | AWS EC2, ECR, SSM, GitHub Actions (OIDC) |

---

## Quick start (Docker)

**Prerequisites:** Docker, Docker Compose, API keys for Groq, Jina, and Qdrant (see `.env.example`).

```bash
cp .env.example .env
# Fill GROQ_API_KEY, JINA_API_KEY, QDRANT_*, NEON_DB_URL, etc.

docker compose up --build -d
```

| Surface | URL |
|---------|-----|
| Chat UI | http://localhost/ |
| API health | http://localhost/api/health |
| API query | `POST http://localhost/api/query` |
| Evals UI | http://localhost/evals/ |

Compose services: `litellm`, `api`, `ui`, `evals`, `nginx`.

### Manual smoke

```bash
SMOKE_BASE_URL=http://127.0.0.1/api bash deploy/smoke_groq_query.sh
```

---

## Configuration

Copy [`.env.example`](./.env.example) → `.env`. Important variables:

| Variable | Purpose |
|----------|---------|
| `LLM_GATEWAY` | `litellm` (default) or `portkey` |
| `LITELLM_BASE_URL` | Proxy URL (`http://litellm:4000` in Compose) |
| `GROQ_API_KEY` / `GROQ_FALLBACK_API_KEY` | Used by LiteLLM |
| `JINA_API_KEY` | Embeddings + rerank |
| `QDRANT_CLUSTER_ENDPOINT` / `QDRANT_API_KEY` | Vector DB |
| `NEON_DB_URL` | LangGraph checkpointer |
| `UPSTASH_REDIS_REST_URL` / `TOKEN` | Rate limiting |
| `RAG_API_KEY` / `RAG_ROLE_KEYS` | API auth & RBAC |
| `BACKEND_URL` | UI → API base URL |

Secrets stay in `.env` (or host `.env` on EC2). They are **not** baked into images.

---

## Tests

```bash
pip install -r requirements-prod.txt
pip install pytest ruff

ruff check app tests
pytest
```

CI runs the same on PRs and as the first Deploy job (`.github/workflows/ci.yml`). Coverage includes auth, guardrails, health, ingestion, rate limits, documents, and eval helpers under `tests/`.

---

## CI/CD

Push to **`main`** → [`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml):

```text
test (ruff + pytest)
  → publish (ECR: app + evals + LiteLLM mirror)
  → deploy (SSM → EC2 remote-update.sh)
  → smoke (health + one live /query)
```

EC2 update script: [`deploy/remote-update.sh`](./deploy/remote-update.sh).

GitHub secrets used by CD (injected onto the instance): `GROQ_API_KEY`, `GROQ_FALLBACK_API_KEY`, `JUDGE_GROQ_API_KEY`, `LITELLM_MASTER_KEY`, optional `RAG_API_KEY`.

---

## Document ingestion

Parse → chunk → embed → Qdrant:

```bash
python -m app.ingestion.processor
```

Source docs live under `DATA/`; processed JSON under `processed_data/`. Optional S3 via `S3_BUCKET` in `.env`.

---

## Evals

Golden dataset and Streamlit eval UI live under `evals/` (also at `/evals/` behind nginx).

```bash
# Pipeline + guardrails against a running API
python -m evals.run_evals
```

Full product/docs detail: [`PROJECT.md`](./PROJECT.md).

---

## Repository layout

```text
app/                 FastAPI, agents, adapters, ingestion, guardrails
ui/                  Streamlit chat
evals/               Golden dataset, pipeline, metrics UI
deploy/              LiteLLM, nginx, EC2 bootstrap/update, smoke
tests/               Pytest suite
.github/workflows/   CI + Deploy
docker-compose.yml   litellm · api · ui · evals · nginx
ARCHITECTURE.md      Diagrams & DI
PROJECT.md           Extended project documentation
```

---

## License

See repository license (if present) or ask the maintainer.

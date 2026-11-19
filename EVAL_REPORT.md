# Evaluation Metric Report

Measured results from the enterprise golden dataset (**15 RAG** + **9 guardrail** samples), plus how **reranker**, **guardrails**, and **chunking** affect each metric.

> **Grade bands used in the eval UI:** ≥ 0.75 Good · 0.50–0.75 Fair · &lt; 0.50 Poor  
> **Judge:** Groq via `JUDGE_GROQ_API_KEY` (RAGAS); Tool Correctness is Jaccard (no LLM).  
> Stage 3 was partially interrupted by Groq TPD/OTPM limits — incomplete metrics are marked.

---

## 1. Metric scorecard (latest measured)

### 1.1 RAGAS / Tool Correctness (Stage 3)

| Metric | Avg | n | Grade | Notes |
|--------|-----|---|-------|-------|
| **Answer Relevancy** | **0.595** | 15 | Fair | Stable across full golden set |
| **Context Precision** | **0.600** | 15 | Fair | Reflects ranking quality after retrieve+rerank; still noisy chunks in top-k |
| **Faithfulness** | **0.059** | 15 | Poor\* | \*Likely judge/context-format artifact (see §5); not a reliable production signal yet |
| Context Recall | — | — | — | Not completed (rate limits) |
| Answer Correctness | — | — | — | Not completed (rate limits) |
| Tool Correctness | — | — | — | Not completed in RAGAS run; see pipeline proxy below |

**Sources:** interrupted JUDGE_GROQ Step 3 run on `_local_pipeline_results.json` (Answer Relevancy / Context Precision / Faithfulness).

### 1.2 Answer Relevancy A/B (prompt + truncate experiment)

| Variant | Avg | n | Δ vs baseline |
|---------|-----|---|---------------|
| Baseline (older response truncate) | **0.646** | 12 | — |
| After prompt / truncate changes | **0.589** | 15 | **−0.057** (no gain) |
| Same 12 questions matched | 0.646 → 0.591 | 12 | **−0.055** |

**Takeaway:** Shortening answers / re-anchoring the responder prompt did **not** improve Answer Relevancy on this golden set. Prefer retrieval/context quality for relevancy gains.

### 1.3 Live pipeline health (Stage 2 proxy for Tool Correctness)

| Run | Tool exact match | Non-empty answers | Notes |
|-----|------------------|-------------------|-------|
| Early EC2 live (pre-fix) | ~2/15 PASS | Many empty / blocked | Planner mismatches, false guardrail blocks, empty answers |
| Local pipeline (`_local_pipeline_results.json`) | **15/15 (1.00)** | **15/15** | All `retrieve_documents` as expected |

**Improvement vs early baseline:** tool routing recovered from sparse passes → **100%** tool exact match on the golden RAG set (local, current stack).

### 1.4 Guardrails (Stage 2)

From `evals/_local_guardrails_results.json` (9 samples):

| | Count |
|---|------|
| TP (correctly blocked) | 5 |
| TN (correctly allowed) | 3 |
| FP (false block) | **0** |
| FN (missed block) | **1** (G1 — SQL injection / exploit ask) |
| **Precision** | **1.000** |
| **Recall** | **0.833** |
| **Accuracy** | **0.889** (8/9) |

**Improvement vs “no guardrails” baseline:** without rails, all 6 adversarial cases would be FP-risk / unblocked → treat as **0 recall on attacks**. Current rails catch **5/6** attack-style cases with **zero** false blocks on the 3 legit K8s questions in this set.

---

## 2. How each component affects metrics

### 2.1 Jina reranker (retrieve 15 → keep top 5)

**Where:** `app/agents/nodes/retriever.py` — Qdrant `limit=15`, then `jina-reranker-v3` `top_n=5`.

| Metric | Expected impact of adding reranker | Observed / status |
|--------|------------------------------------|-------------------|
| **Context Precision** | **Primary win** — relevant chunks move above noise | Measured **0.60** (Fair). Still room: noisy/off-doc chunks appear in contexts |
| **Context Recall** | Mild help if good chunk was in the 15 but buried; can **hurt** if reranker drops a needed chunk outside top 5 | Not fully measured |
| **Faithfulness** | Indirect — cleaner context → less hallucination | Unreliable score this run |
| **Answer Relevancy / Correctness** | Indirect via better evidence | Relevancy **0.595** with reranker on |
| **Tool Correctness** | None (tool choice is planner/guardrails) | — |
| **Guardrail P/R** | None | — |

**Without reranker (design baseline):** top-15 by vector score only → typically **lower Context Precision** when the corpus mixes `true_data` + `noisy_data` PDFs.

**With reranker (current):** semantic re-ordering against the query; main lever for Context Precision on this corpus.

### 2.2 NeMo Guardrails (pre-graph gate)

**Where:** `QueryService` → `NemoGuardrailsAdapter` before LangGraph.

| Metric | Impact |
|--------|--------|
| **Guardrail Precision / Recall / Accuracy** | **Direct** — see §1.4 (P=1.0, R=0.83, Acc=0.89) |
| **Faithfulness / Relevancy / Correctness** | On blocked turns, RAGAS is **skipped** (refusals not scored as answers) — protects averages from jailbreak garbage |
| **Tool Correctness** | Blocked turns record as guardrails path, not `retrieve_documents` — good for safety, fails tool match if a golden “legit” Q is over-blocked |
| **Context Precision / Recall** | N/A when blocked (no retrieval) |

**Baseline without guardrails:** adversarial prompts reach the LLM → risk of unsafe answers; RAGAS answer metrics become noisy / undefined for attacks.  
**With guardrails:** clear safety metrics; one remaining FN (G1) is the main gap.

### 2.3 Chunking strategy

**Where:** `app/ingestion/chunking/splitter.py`

| Setting | Value |
|---------|-------|
| `chunk_size` | **1500** chars |
| `overlap` | **200** chars |
| Strategy | Paragraph/sentence-aware packing; page spans preserved; hard cap at `chunk_size` |

| Metric | How chunking affects it |
|--------|-------------------------|
| **Context Recall** | Too-small chunks → split facts across pieces (need more top-k). Too-large → diluted embeddings, miss in retrieval |
| **Context Precision** | Huge chunks mix topics → reranker/vector scores blur; small chunks + good overlap usually help |
| **Faithfulness** | Oversized mixed chunks encourage the model to use unrelated sentences in-context |
| **Answer Correctness** | Incomplete chunks → missing steps (e.g. multi-step kubectl procedures) |
| **Answer Relevancy** | Indirect via answer content quality |

**Current choice (1500 / 200):** balances procedure-style K8s docs (often multi-paragraph) against embedding noise.  
**Formal A/B** (e.g. 500/50 vs 1500/200 vs 3000/300) was **not** completed under rate limits — treat as **design rationale**, not a measured delta table.

---

## 3. Baseline → current (summary)

| Area | Baseline (earlier / ablated) | Current stack | Improvement |
|------|------------------------------|---------------|-------------|
| Tool routing (golden RAG) | Early live: many `direct_answer` / empty / false guardrail blocks | Local: **15/15** `retrieve_documents` | **Large** operational fix |
| Guardrails | None / weak → attacks reach model | Acc **0.889**, P **1.0**, R **0.833** | Strong safety; 1 FN left |
| Retrieval ranking | Vector-only top-k | + **Jina rerank** top 5 of 15 | Context Precision **0.60** (Fair); main lever still open |
| Answer Relevancy | 0.646 (older truncate) | 0.589–0.595 after prompt tweaks | **No gain** from that experiment |
| Chunking | Ad-hoc | Fixed **1500 / overlap 200** | Stable ingest; A/B pending |
| Full RAGAS suite | Incomplete | Partial (3/6 metrics) | Re-run when Groq TPD allows |

---

## 4. Priority map (what to optimize next)

| Goal | Primary levers | Metrics to watch |
|------|----------------|------------------|
| Safer product | Fix G1 FN; tighten jailbreak rails without raising FP | Guardrail Recall ↑, Precision stay ~1.0 |
| Better evidence | Rerank tuning / hybrid search / filter noisy corpus | Context Precision ↑, then Recall |
| Better answers | Context quality first; then responder prompt | Correctness, Faithfulness, Relevancy |
| Avoid wasted work | Don’t chase Relevancy via truncate alone | Relevancy A/B already flat/negative |

---

## 5. Caveats

1. **Faithfulness 0.059** is not trusted as a true system score — earlier runs fed RAGAS wrapped `CONTENT: {'text': ...}` contexts; normalization was added later. Re-score after a clean pipeline snapshot.
2. **No completed with/without-reranker or chunk-size A/B** under the same judge/settings — section 2 states **expected** effects; only Precision/Relevancy/Guardrails/Tool proxy are measured.
3. **CD smoke ≠ eval report** — Deploy only checks one live `/query`.
4. Re-run recipe: Stage 2 → `evals/_local_pipeline_results.json` → Stage 3 `python -m evals` / evals UI / `_run_local_step3.py` when limits restore; refresh this file’s tables.

---

## 6. Artifact index

| File | Contents |
|------|----------|
| `evals/golden_dataset.json` | 15 RAG + 9 guardrail goldens |
| `evals/_local_pipeline_results.json` | Live `/query` enrichments (current local) |
| `evals/_local_guardrails_results.json` | Guardrail confusion matrix |
| `evals/metrics.py` | RAGAS Stage 3 implementation |
| `evals/pipeline.py` | Stage 2 live calls |
| `evals/guardrails_eval.py` | Guardrail scoring |
| `app/ingestion/chunking/splitter.py` | Chunk size / overlap |
| `app/agents/nodes/retriever.py` | Retrieve 15 + rerank 5 |

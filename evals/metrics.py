"""
Phase 2 — RAGAS + Tool Correctness metrics.
Judge LLM prefers JUDGE_GROQ_API_KEY (direct Groq API). If unset, falls back to
the active LLM gateway (LiteLLM proxy by default). Embeddings use Jina (no OpenAI).
All LLM-based metrics run one sample at a time with short cooldowns between sub-batches
and experiments as a conservative rate-limit buffer.
Contexts are truncated to 300 chars (2 chunks max) so no single request exceeds the limit.
"""

import asyncio

import pandas as pd
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI
from ragas.embeddings import BaseRagasEmbedding
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerCorrectness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

from app.adapters.jina_embeddings import JinaEmbedder
from app.config import settings
from app.gateway.client import get_gateway
from app.observe import info, span

_GROQ_OPENAI_BASE = "https://api.groq.com/openai/v1"

COOLDOWN_STANDARD = 62
COOLDOWN_MINI = 40  # between individual samples — conservative rate-limit buffer
GENERAL_BATCH_SIZE = 1  # one sample at a time: abatch_score fires calls concurrently per sample,
# so batch>1 stacks multiple samples' async calls inside the same second
CONTEXT_TRUNCATE = 500  # chars per chunk after cleanup (keeps judge prompts under Groq OTPM)
CONTEXT_LIMIT = 2  # number of context chunks passed to RAGAS per sample

# Pure refusals / jailbreak text are not answerable for Answer Relevancy and
# would drag the suite average without measuring answer quality.
_NON_ANSWER_PREFIXES = (
    "i'm an enterprise it assistant",
    "i can only answer from the company documents",
    "i can only help with",
)


def _is_scorable_answer(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    lower = cleaned.lower()
    return not any(lower.startswith(p) for p in _NON_ANSWER_PREFIXES)


def _normalize_context(chunk: str) -> str:
    """Strip retriever wrappers so RAGAS sees document text, not ``CONTENT: {..}.``"""
    import ast
    import re

    text = (chunk or "").strip()
    if text.upper().startswith("CONTENT:"):
        text = text.split(":", 1)[1].strip()
    if text.startswith("{") and "'text'" in text:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict) and parsed.get("text"):
                text = str(parsed["text"])
        except (SyntaxError, ValueError):
            m = re.search(r"['\"]text['\"]\s*:\s*['\"](.+)", text, re.S)
            if m:
                text = m.group(1)
    return text.strip()


class _JinaRagasEmbeddings(BaseRagasEmbedding):
    """Modern ragas embeddings backed by the app's Embedder port (Jina)."""

    def __init__(self, embedder: JinaEmbedder | None = None):
        self._embedder = embedder or JinaEmbedder()

    def embed_text(self, text: str, **kwargs) -> list[float]:
        return self._embedder.embed_query(text)

    async def aembed_text(self, text: str, **kwargs) -> list[float]:
        return self._embedder.embed_query(text)

    def embed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
        return self._embedder.embed_texts(texts)

    async def aembed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
        return self._embedder.embed_texts(texts)


def _build_judge():
    """Build RAGAS judge LLM + Jina embeddings.

    Prefer direct Groq when ``JUDGE_GROQ_API_KEY`` is set so evals do not share
    the Portkey→Groq TPD bucket used by the live API. max_tokens must be high
    enough for RAGAS instructor JSON (NLI verdicts truncate at defaults).
    """
    groq_key = settings.judge_groq_api_key
    if groq_key:
        model = (settings.JUDGE_GROQ_MODEL or "openai/gpt-oss-20b").strip()
        info(f"RAGAS judge: direct Groq API ({model})")
        client = wrap_openai(
            AsyncOpenAI(api_key=groq_key, base_url=_GROQ_OPENAI_BASE),
            chat_name="Groq RAGAS judge",
        )
        llm = llm_factory(
            model,
            provider="openai",
            client=client,
            max_tokens=4096,
        )
        return llm, _JinaRagasEmbeddings(JinaEmbedder())

    info("RAGAS judge: LLM gateway fallback alias (set JUDGE_GROQ_API_KEY for direct Groq)")
    gateway = get_gateway()
    client = gateway.get_async_judge_client()
    llm = llm_factory(
        gateway.judge_model_id(),
        provider="openai",
        client=client,
        max_tokens=4096,
    )
    return llm, _JinaRagasEmbeddings(JinaEmbedder())


async def _cooldown(seconds: int, label: str, status_cb=None):
    msg = f"⏳ {seconds}s cooldown after {label} (rate-limit buffer)..."
    if status_cb:
        status_cb(msg)
    for _ in range(seconds // 10):
        await asyncio.sleep(10)
    if status_cb:
        status_cb("✅ Ready — starting next experiment.")


def _prep_samples(golden_dataset: dict) -> list:
    """
    Returns samples with a real (non-refusal) actual_response.
    Truncates contexts to CONTEXT_TRUNCATE chars and limits to CONTEXT_LIMIT chunks
    so a single RAGAS LLM call stays small and fast.
    """
    valid = []
    for s in golden_dataset["rag_samples"]:
        response = s.get("actual_response", "").strip()
        if not _is_scorable_answer(response):
            continue
        raw_contexts = s.get("actual_contexts") or s.get("relevant_contexts") or []
        contexts = [
            _normalize_context(c)[:CONTEXT_TRUNCATE] for c in raw_contexts[:CONTEXT_LIMIT] if str(c).strip()
        ]
        contexts = [c for c in contexts if c]
        valid.append({**s, "actual_contexts": contexts})
    return valid


def _score_df(metric_key: str, samples: list, scores) -> pd.DataFrame:
    rows = []
    for s, r in zip(samples, scores):
        try:
            val = float(r.value)
        except (TypeError, ValueError, AttributeError):
            val = float("nan")
        rows.append({"question": s["question"][:65], metric_key: None if val != val else round(val, 3)})
    return pd.DataFrame(rows)


async def _batched_score(metric, inputs: list, samples: list, status_cb=None, label: str = "") -> list:
    """
    Runs abatch_score in chunks of GENERAL_BATCH_SIZE with cooldowns between chunks.
    Keeps bursts small as a conservative rate-limit buffer.
    Retries on provider 429 with a parsed wait (capped) so TPD windows can recover.
    """
    import re

    all_scores = []
    batches = [inputs[i : i + GENERAL_BATCH_SIZE] for i in range(0, len(inputs), GENERAL_BATCH_SIZE)]
    for b_idx, batch in enumerate(batches):
        if b_idx > 0:
            await _cooldown(COOLDOWN_MINI, f"{label} batch {b_idx}", status_cb)
        attempt = 0
        while True:
            try:
                scores = await metric.abatch_score(batch)
                all_scores.extend(scores)
                break
            except Exception as exc:
                msg = str(exc)
                attempt += 1
                is_rl = any(
                    x in msg.lower()
                    for x in ("rate_limit", "429", "tpd", "tokens per day", "rate limit", "otpm", "tokens per minute")
                )
                # Groq formats: "try again in 7m24.528s" or "try again in 12.5s"
                m = re.search(
                    r"try again in\s+(?:(\d+)m)?([\d.]+)s",
                    msg,
                    re.I,
                )
                if is_rl and attempt <= 6:
                    if m:
                        mins = int(m.group(1) or 0)
                        secs = float(m.group(2) or 0)
                        wait_s = min(int(mins * 60 + secs) + 20, 1200)
                    else:
                        wait_s = min(30 * attempt, 120)
                    if status_cb:
                        status_cb(
                            f"⏳ rate-limit on {label} batch {b_idx}; sleeping {wait_s}s (attempt {attempt}/6)"
                        )
                    await asyncio.sleep(wait_s)
                    continue
                # Soft-fail a single sample so one bad judge response doesn't abort Step 3.
                soft = any(
                    x in msg.lower()
                    for x in (
                        "json_validate_failed",
                        "failed to validate json",
                        "instructorretry",
                        "failed_generation",
                        "invalid_request_error",
                        "incompleteoutput",
                        "incomplete due to a max_tokens",
                        "model_not_found",
                    )
                )
                if soft and len(batch) == 1:
                    if status_cb:
                        status_cb(f"⚠️ {label} sample judge failed; recording null score")

                    class _Null:
                        value = float("nan")

                    all_scores.append(_Null())
                    break
                if soft and len(batch) > 1:
                    for item in batch:
                        one = await _batched_score(metric, [item], samples[:1], status_cb, label)
                        all_scores.extend(one)
                    break
                raise
    return all_scores


async def run_all_metrics(golden_dataset: dict, status_cb=None) -> dict:
    """
    Runs all 6 experiments. Returns dict keyed by metric name → DataFrame.
    status_cb(message: str) is called for live UI updates.
    """
    judge_llm, ragas_embeddings = _build_judge()
    samples = _prep_samples(golden_dataset)

    if not samples:
        raise ValueError("No samples with actual_response found. Run Phase 1 first.")

    results = {}

    with span("🧪 Eval Phase 2 — All Metrics", total_samples=len(samples)):
        # ── Exp 1: Faithfulness ───────────────────────────────────────────────
        if status_cb:
            status_cb(f"🧪 Exp 1/6 — Faithfulness ({len(samples)} samples)...")
        with span("🧪 Exp 1 — Faithfulness"):
            inputs = [
                {
                    "user_input": s["question"],
                    "response": s["actual_response"],
                    "retrieved_contexts": s["actual_contexts"],
                }
                for s in samples
            ]
            scores = await _batched_score(Faithfulness(llm=judge_llm), inputs, samples, status_cb, "Faithfulness")
            df = _score_df("faithfulness", samples, scores)
            results["faithfulness"] = df
            avg = round(float(df["faithfulness"].mean()), 3)
            info("🧪 Faithfulness done", avg=avg)
            if status_cb:
                status_cb(f"✅ Faithfulness avg={avg} n={len(df)}")

        await _cooldown(COOLDOWN_STANDARD, "Faithfulness", status_cb)

        # ── Exp 2: Answer Relevancy ───────────────────────────────────────────
        if status_cb:
            status_cb(f"🧪 Exp 2/6 — Answer Relevancy ({len(samples)} samples)...")
        with span("🧪 Exp 2 — Answer Relevancy"):
            inputs = [{"user_input": s["question"], "response": s["actual_response"]} for s in samples]
            scores = await _batched_score(
                AnswerRelevancy(llm=judge_llm, embeddings=ragas_embeddings),
                inputs,
                samples,
                status_cb,
                "Answer Relevancy",
            )
            df = _score_df("answer_relevancy", samples, scores)
            results["answer_relevancy"] = df
            avg = round(float(df["answer_relevancy"].mean()), 3)
            info("🧪 Answer Relevancy done", avg=avg)
            if status_cb:
                status_cb(f"✅ Answer Relevancy avg={avg} n={len(df)}")

        await _cooldown(COOLDOWN_STANDARD, "Answer Relevancy", status_cb)

        # ── Exp 3: Context Precision ──────────────────────────────────────────
        if status_cb:
            status_cb(f"🧪 Exp 3/6 — Context Precision ({len(samples)} samples)...")
        with span("🧪 Exp 3 — Context Precision"):
            inputs = [
                {
                    "user_input": s["question"],
                    "reference": s["reference"],
                    "retrieved_contexts": s["actual_contexts"],
                }
                for s in samples
            ]
            scores = await _batched_score(
                ContextPrecision(llm=judge_llm), inputs, samples, status_cb, "Context Precision"
            )
            df = _score_df("context_precision", samples, scores)
            results["context_precision"] = df
            avg = round(float(df["context_precision"].mean()), 3)
            info("🧪 Context Precision done", avg=avg)
            if status_cb:
                status_cb(f"✅ Context Precision avg={avg} n={len(df)}")

        await _cooldown(COOLDOWN_STANDARD, "Context Precision", status_cb)

        # ── Exp 4: Context Recall ─────────────────────────────────────────────
        if status_cb:
            status_cb(f"🧪 Exp 4/6 — Context Recall ({len(samples)} samples)...")
        with span("🧪 Exp 4 — Context Recall"):
            inputs = [
                {
                    "user_input": s["question"],
                    "reference": s["reference"],
                    "retrieved_contexts": s["actual_contexts"],
                }
                for s in samples
            ]
            scores = await _batched_score(ContextRecall(llm=judge_llm), inputs, samples, status_cb, "Context Recall")
            df = _score_df("context_recall", samples, scores)
            results["context_recall"] = df
            avg = round(float(df["context_recall"].mean()), 3)
            info("🧪 Context Recall done", avg=avg)
            if status_cb:
                status_cb(f"✅ Context Recall avg={avg} n={len(df)}")

        await _cooldown(COOLDOWN_STANDARD, "Context Recall", status_cb)

        # ── Exp 5: Answer Correctness (split into batches) ────────────────────
        if status_cb:
            status_cb("🧪 Exp 5/6 — Answer Correctness...")
        with span("🧪 Exp 5 — Answer Correctness"):
            inputs = [
                {
                    "user_input": s["question"],
                    "response": s["actual_response"],
                    "reference": s["reference"],
                }
                for s in samples
            ]
            all_scores = await _batched_score(
                AnswerCorrectness(llm=judge_llm, embeddings=ragas_embeddings),
                inputs,
                samples,
                status_cb,
                "Answer Correctness",
            )
            df = _score_df("answer_correctness", samples, all_scores)
            results["answer_correctness"] = df
            avg = round(float(df["answer_correctness"].mean()), 3)
            info("🧪 Answer Correctness done", avg=avg)
            if status_cb:
                status_cb(f"✅ Answer Correctness avg={avg} n={len(df)}")

        await _cooldown(COOLDOWN_STANDARD, "Answer Correctness", status_cb)

        # ── Exp 6: Tool Correctness (no LLM — Jaccard) ───────────────────────
        if status_cb:
            status_cb("⚡ Exp 6/6 — Tool Correctness (zero LLM calls)...")
        with span("🧪 Exp 6 — Tool Correctness"):
            tool_rows = []
            for s in samples:
                called = set(s.get("actual_tools_called") or [])
                expected = set(s.get("expected_tools") or [])
                union = len(called | expected)
                score = len(called & expected) / union if union > 0 else 0.0
                tool_rows.append({"question": s["question"][:65], "tool_correctness": round(score, 3)})
            df = pd.DataFrame(tool_rows)
            results["tool_correctness"] = df
            avg = round(float(df["tool_correctness"].mean()), 3)
            info("🧪 Tool Correctness done", avg=avg)
            if status_cb:
                status_cb(f"✅ Tool Correctness avg={avg} n={len(df)}")

        if status_cb:
            status_cb("✅ All 6 experiments complete!")

    return results

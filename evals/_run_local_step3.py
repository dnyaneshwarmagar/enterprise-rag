"""Local-only: pipeline against localhost:8000 (if healthy), then RAGAS Step 3.

Judge prefers JUDGE_GROQ_API_KEY (direct Groq); else Portkey fallback slug.
Embeddings: Jina. If local pipeline can't fill answers, falls back to
evals/_live_pipeline_results.json. Set SKIP_PIPELINE=1 to score prior answers only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
os.environ["BACKEND_URL"] = "http://127.0.0.1:8000"
os.environ["PYTHONPATH"] = str(ROOT)

import evals.pipeline as pipeline_mod
import evals.metrics as metrics_mod
from evals.pipeline import load_golden_dataset, run_pipeline
from evals.metrics import run_all_metrics

# Local Groq-friendly pacing (TPD / RPM buffer).
pipeline_mod.DELAY_BETWEEN_CALLS = 20
metrics_mod.COOLDOWN_STANDARD = 45
metrics_mod.COOLDOWN_MINI = 25
if os.getenv("LOCAL_EVAL_FAST", "").strip() in {"1", "true", "yes"}:
    metrics_mod.COOLDOWN_STANDARD = 20
    metrics_mod.COOLDOWN_MINI = 12
    print("LOCAL_EVAL_FAST=1 — shorter RAGAS cooldowns", flush=True)

OUT_DIR = Path(__file__).resolve().parent
PIPELINE_OUT = OUT_DIR / "_local_pipeline_results.json"
FALLBACK_PIPELINE = OUT_DIR / "_live_pipeline_results.json"
METRICS_OUT = OUT_DIR / "_local_step3_results.json"


def _load_pipeline_answers() -> dict:
    """Prefer newest local pipeline file, then older live snapshot."""
    for path in (PIPELINE_OUT, FALLBACK_PIPELINE):
        if path.exists():
            print(f"Loading pipeline answers from {path.name}", flush=True)
            return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit("No pipeline answers available for Step 3")


def _progress(i, total, question, stage, response=""):
    if stage == "calling":
        print(f"[{i + 1}/{total}] calling {question[:70]!r}", flush=True)
    elif stage == "done":
        print(f"[{i + 1}/{total}] done len={len(response or '')}", flush=True)


def _usable(dataset: dict) -> list:
    from evals.metrics import _is_scorable_answer

    rows = []
    for s in dataset.get("rag_samples", []):
        if not _is_scorable_answer(s.get("actual_response") or ""):
            continue
        rows.append(s)
    return rows


def main() -> None:
    print("BACKEND_URL=", os.environ["BACKEND_URL"], flush=True)
    enriched = None

    skip_pipeline = os.getenv("SKIP_PIPELINE", "").strip() in {"1", "true", "yes"}
    if skip_pipeline:
        print("SKIP_PIPELINE=1 — using prior pipeline answers for Step 3", flush=True)
        enriched = _load_pipeline_answers()
    else:
        try:
            import requests

            health = requests.get("http://127.0.0.1:8000/health", timeout=10)
            health.raise_for_status()
            print("Local API healthy — running Step 2 pipeline...", flush=True)
            enriched = run_pipeline(load_golden_dataset(), progress_callback=_progress)
            PIPELINE_OUT.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
            print("saved", PIPELINE_OUT, flush=True)
        except Exception as exc:
            print(f"Local pipeline skipped/failed: {exc}", flush=True)

    if enriched is None or len(_usable(enriched)) < 5:
        enriched = _load_pipeline_answers()

    usable = _usable(enriched)
    print(f"samples with scorable answers: {len(usable)}/{len(enriched['rag_samples'])}", flush=True)
    if not usable:
        raise SystemExit("No answers to score — aborting Step 3")

    # Optional local limit (Groq TPD). 0 / unset-with-ALL = full set.
    # Default 0 when SKIP_PIPELINE (reuse full local pipeline); else 4 for live runs.
    limit_raw = os.getenv("STEP3_SAMPLE_LIMIT", "0" if skip_pipeline else "4").strip()
    try:
        limit = int(limit_raw) if limit_raw else 0
    except ValueError:
        limit = 0
    if limit > 0 and len(usable) > limit:
        print(f"STEP3_SAMPLE_LIMIT={limit} — scoring first {limit} scorable samples", flush=True)
        usable = usable[:limit]

    # Keep only scorable samples for metrics (RAGAS requires actual_response).
    enriched = {**enriched, "rag_samples": usable}

    print("Step 3 judge: JUDGE_GROQ (preferred) / LiteLLM fallback + Jina embeddings", flush=True)
    print("Step 3: RAGAS metrics...", flush=True)


    def status_cb(msg: str) -> None:
        print(msg, flush=True)

    results = asyncio.run(run_all_metrics(enriched, status_cb=status_cb))

    summary = {}
    serializable = {}
    for name, df in results.items():
        avg = float(df.iloc[:, 1].mean()) if len(df) else None
        summary[name] = {"avg": round(avg, 3) if avg is not None else None, "n": int(len(df))}
        serializable[name] = df.to_dict(orient="records")
        print(f"  {name}: avg={summary[name]['avg']} n={summary[name]['n']}", flush=True)

    payload = {"summary": summary, "details": serializable, "n_samples": len(usable)}
    METRICS_OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("saved", METRICS_OUT, flush=True)
    print("SUMMARY", json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

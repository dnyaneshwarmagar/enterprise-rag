from evals.guardrails_eval import compute_guardrails_metrics, run_guardrails_eval
from evals.pipeline import load_golden_dataset, run_pipeline

__all__ = [
    "compute_guardrails_metrics",
    "run_guardrails_eval",
    "load_golden_dataset",
    "run_pipeline",
    "run_all_metrics",
]


def run_all_metrics(*args, **kwargs):
    """Lazy-import so `import evals` works without ragas installed."""
    from evals.metrics import run_all_metrics as _run_all_metrics

    return _run_all_metrics(*args, **kwargs)

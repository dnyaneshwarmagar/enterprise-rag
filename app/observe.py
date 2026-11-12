"""LangSmith traces and local logs.

This is the only observability path. A span becomes a LangSmith run and
nests under the current trace when one is already open. Info, warning, and
error lines stay in the process log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import trace

logger = logging.getLogger("enterprise_rag")


class SpanRecord:
    """Outcome writer for one LangSmith run. A missing run still lets the work proceed."""

    def __init__(self, run: Any | None):
        self._run = run

    def outputs(self, **fields: Any) -> None:
        """Attach a success or failure result to this span."""
        if self._run is None:
            return
        clean = {key: value for key, value in fields.items() if value is not None}
        if not clean:
            return
        try:
            self._run.add_outputs(clean)
        except Exception:
            logger.warning("LangSmith outputs were not recorded")


@contextmanager
def span(name: str, **inputs: Any) -> Iterator[SpanRecord]:
    """Record a LangSmith run. The enclosed work still runs if tracing is down."""
    clean = {key: value for key, value in inputs.items() if value is not None}
    try:
        context = trace(name, inputs=clean or None)
    except Exception:
        logger.warning("LangSmith span %s was not opened", name)
        yield SpanRecord(None)
        return
    with context as run:
        yield SpanRecord(run)


def info(message: str, **fields: Any) -> None:
    logger.info(_format(message, fields))


def warning(message: str, **fields: Any) -> None:
    logger.warning(_format(message, fields))


def error(message: str, **fields: Any) -> None:
    logger.error(_format(message, fields))


def _format(message: str, fields: dict[str, Any]) -> str:
    if not fields:
        return message
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    return f"{message} {extra}"

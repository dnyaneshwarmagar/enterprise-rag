"""Guardrails port."""

from __future__ import annotations

from typing import Protocol


class GuardrailsPort(Protocol):
    """Input safety checks before the RAG graph runs."""

    def initialize(self) -> None:
        """Build any heavy resources (NeMo rails) at process startup."""

    def check(self, message: str) -> tuple[bool, str | None]:
        """
        Returns:
            (True, response) — blocked; return response to the client.
            (False, None) — allowed; continue to the agent.
        """

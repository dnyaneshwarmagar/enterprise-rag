"""NeMo Guardrails adapter — dynamic lookup so tests can patch rails.guard."""

from __future__ import annotations


class NemoGuardrailsAdapter:
    """GuardrailsPort that delegates to app.guardrails.rails."""

    def initialize(self) -> None:
        from app.guardrails.rails import initialize_rails

        initialize_rails()

    def check(self, message: str) -> tuple[bool, str | None]:
        # Import on each call so unittest.mock.patch("app.guardrails.rails.guard") works.
        from app.guardrails.rails import guard

        return guard(message)

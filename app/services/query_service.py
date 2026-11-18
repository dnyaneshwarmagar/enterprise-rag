"""Application service for /query."""

from __future__ import annotations

from typing import Any, Callable

from app.ports.guardrails import GuardrailsPort


class QueryService:
    """Guardrails → LangGraph → response DTO."""

    def __init__(
        self,
        guardrails: GuardrailsPort,
        get_agent: Callable[[], Any],
    ):
        self._guardrails = guardrails
        self._get_agent = get_agent

    def ask(self, question: str, roles: list[str] | tuple[str, ...], thread_id: str) -> dict:
        """
        Run one user question.

        Returns either a blocked payload or a normal RAG result dict.
        Raises on unexpected graph failures (caller maps to HTTP 500).
        """
        rail_fired, rail_response = self._guardrails.check(question)
        if rail_fired:
            return {
                "blocked": True,
                "question": question,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": [],
            }

        agent = self._get_agent()
        initial_state = {
            "messages": [{"role": "user", "content": question}],
            "current_query": question,
            "documents": [],
            "plan": ["Start"],
            "status": "Initializing Graph...",
            "access_roles": list(roles),
        }
        config = {"configurable": {"thread_id": thread_id}}
        final_output = agent.invoke(initial_state, config=config)
        return {
            "blocked": False,
            "question": question,
            "answer": final_output.get("final_answer"),
            "thought_process": final_output.get("plan"),
            "status": final_output.get("status"),
            "sources": final_output.get("documents", []),
        }

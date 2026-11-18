import re
from typing import Any, Callable

from app.agents.state import AgentState
from app.gateway import get_langchain_llm
from app.observe import info, span

# If the user message looks technical, never skip retrieval (even if the LLM says CONVERSATIONAL).
_TECH_HINTS = re.compile(
    r"\b("
    r"kubernetes|k8s|kubectl|kube-|pod|pods|job|jobs|cronjob|cronjobs|"
    r"hpa|vpa|autoscal|minikube|metrics.?server|redis|databricks|"
    r"parallelism|completions|docker|helm|ingress|deployment|replica|"
    r"intel|sriov|fpga|vlan|bgp|sdn|networking|container"
    r")\b",
    re.IGNORECASE,
)


def make_planner_node(llm: Any) -> Callable[[AgentState], dict]:
    """Build a planner node closed over an injectable LangChain chat model."""

    def planner_node(state: AgentState):
        """
        The Planner determines if a search is needed based on the ENTIRE conversation.
        """
        history = ""
        for msg in state["messages"][:-1]:
            role = "User" if msg["role"] == "user" else "Assistant"
            history += f"{role}: {msg['content']}\n"

        user_message = state["messages"][-1]["content"] if state["messages"] else ""
        force_retrieve = bool(_TECH_HINTS.search(user_message or ""))

        prompt = f"""
    You are an intelligent Assistant Planner.
    Analyze the conversation history and the latest user message.

    CONVERSATION HISTORY:
    {history}

    LATEST MESSAGE:
    "{user_message}"

    Task:
    1. Output 'CONVERSATIONAL' ONLY for greetings (hi, hello), thanks/goodbye, or a question that can be answered ONLY from the conversation history above (e.g. "what did I just ask").
    2. For ANY technical / how-to / documentation question — including Kubernetes, containers, pods, Jobs, CronJobs, Redis work queues, Minikube, Metrics Server, kubectl, Databricks jobs/CLI/SDK/REST API, Intel hardware, or networking — output a short refined search query.
       Never answer these from model memory. Always search company docs.

    Output ONLY 'CONVERSATIONAL' or the search query. No explanation.
    """

        with span("🧠 Planner Decision"):
            decision = llm.invoke(prompt).content.strip().strip('"').strip("'")
            info(f"Intent identified: {decision}")

        conversational = decision.upper() == "CONVERSATIONAL" or decision.upper().startswith("CONVERSATIONAL")
        if conversational and not force_retrieve:
            return {
                "current_query": "CONVERSATIONAL",
                "status": "Handling conversationally (using memory)...",
                "plan": ["Intent: Conversational/Memory", "Retrieval: Skipped"],
            }

        search_term = user_message if conversational else decision
        return {
            "current_query": search_term,
            "status": f"Technical research needed. Searching for: {search_term}",
            "plan": ["Intent: Technical", f"Search Term: {search_term}"],
        }

    return planner_node


# Module-level default for imports/tests that still reference planner_node directly.
planner_node = make_planner_node(get_langchain_llm(feature="planner"))

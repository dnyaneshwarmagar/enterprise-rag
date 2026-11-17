from app.agents.state import AgentState
from app.gateway import get_langchain_llm
from app.observe import info, span

# Portkey-backed LLM: fallback + cache + retry — same .invoke() interface as ChatOpenAI
llm = get_langchain_llm(feature="planner")


def planner_node(state: AgentState):
    """
    The Planner determines if a search is needed based on the ENTIRE conversation.
    """
    # Get the conversation history (excluding the latest message)
    history = ""
    for msg in state["messages"][:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history += f"{role}: {msg['content']}\n"

    user_message = state["messages"][-1]["content"] if state["messages"] else ""

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

    # Guard against models wrapping the token in prose.
    if decision.upper() == "CONVERSATIONAL" or decision.upper().startswith("CONVERSATIONAL"):
        return {
            "current_query": "CONVERSATIONAL",
            "status": "Handling conversationally (using memory)...",
            "plan": ["Intent: Conversational/Memory", "Retrieval: Skipped"],
        }

    return {
        "current_query": decision,
        "status": f"Technical research needed. Searching for: {decision}",
        "plan": ["Intent: Technical", f"Search Term: {decision}"],
    }

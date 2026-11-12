from unittest.mock import patch

from app.agents.nodes.retriever import retrieve_node


def test_retriever_passes_caller_roles_into_search():
    state = {
        "current_query": "leave policy",
        "documents": [],
        "plan": [],
        "messages": [],
        "status": "",
        "final_answer": "",
        "access_roles": ["hr"],
    }
    with patch("app.agents.nodes.retriever.search_enterprise_knowledge", return_value=[]) as search:
        retrieve_node(state)

    assert search.call_args.kwargs["roles"] == ["hr"]

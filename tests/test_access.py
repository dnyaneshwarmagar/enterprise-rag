import json

from app.access import access_filter, resolve_roles, roles_for_document
from app.config import settings


def test_last_matching_rule_assigns_document_roles(tmp_path):
    policy = tmp_path / "access_policy.json"
    policy.write_text(
        json.dumps(
            {
                "default_roles": ["employee"],
                "rules": [
                    {"match": "true/*", "roles": ["employee", "hr"]},
                    {"match": "true/restricted/*", "roles": ["hr"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert roles_for_document("true", "guide.pdf", path=str(policy)) == ["employee", "hr"]
    assert roles_for_document("true", "restricted/pay.pdf", path=str(policy)) == ["hr"]
    assert roles_for_document("noisy", "note.txt", path=str(policy)) == ["employee"]


def test_access_filter_hides_other_roles_and_opens_for_admin():
    assert access_filter(["*"]) is None

    employee = access_filter(["employee"])
    assert employee.should[0].match.any == ["employee"]
    assert len(employee.should) == 2

    hr_only = access_filter(["hr"])
    assert hr_only.should[0].match.any == ["hr"]
    assert len(hr_only.should) == 1

    denied = access_filter([])
    assert denied.should[0].match.any == ["__no_access__"]


def test_demo_role_is_used_only_while_the_api_is_open():
    from app.access import apply_demo_role

    original_key = settings.API_KEY
    original_map = settings.RAG_ROLE_KEYS
    original_demo = settings.RBAC_DEMO
    try:
        settings.API_KEY = None
        settings.RAG_ROLE_KEYS = ""
        settings.RBAC_DEMO = True
        assert apply_demo_role(("employee",), "hr") == ("hr",)
        assert apply_demo_role(("employee",), "employee") == ("employee",)
        assert apply_demo_role(("employee",), "owner") == ("employee",)
        settings.API_KEY = "master-secret"
        assert apply_demo_role(("*",), "hr") == ("*",)
    finally:
        settings.API_KEY = original_key
        settings.RAG_ROLE_KEYS = original_map
        settings.RBAC_DEMO = original_demo


def test_resolve_roles_uses_the_token_not_a_client_supplied_role():
    original_key = settings.API_KEY
    original_map = settings.RAG_ROLE_KEYS
    original_master_roles = settings.RAG_API_KEY_ROLES
    try:
        settings.API_KEY = "master-secret"
        settings.RAG_API_KEY_ROLES = "*"
        settings.RAG_ROLE_KEYS = "hr-secret=hr,manager"
        assert resolve_roles(None) is None
        assert resolve_roles("hr-secret") == ("hr", "manager")
        assert resolve_roles("master-secret") == ("*",)
        assert resolve_roles("employee") is None
    finally:
        settings.API_KEY = original_key
        settings.RAG_ROLE_KEYS = original_map
        settings.RAG_API_KEY_ROLES = original_master_roles

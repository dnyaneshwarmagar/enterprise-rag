"""Role-based document access.

Roles are assigned to files at ingestion time and to callers from their
bearer token. A request cannot choose its own roles.
"""

from __future__ import annotations

import json
import os
from fnmatch import fnmatch

from qdrant_client.http import models

from app.config import settings
from app.ingestion.identity import document_key

ADMIN_ROLE = "*"
_DENY_ROLE = "__no_access__"


def parse_role_list(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated role list. Role names are stored lowercase."""
    if not raw:
        return ()
    roles: list[str] = []
    for part in raw.split(","):
        role = part.strip().lower()
        if role == ADMIN_ROLE:
            roles.append(ADMIN_ROLE)
        elif role:
            roles.append(role)
    return tuple(dict.fromkeys(roles))


def role_keys() -> dict[str, tuple[str, ...]]:
    """Map limited bearer tokens to roles.

    ``RAG_ROLE_KEYS`` is ``token=role,role;token2=hr``.
    """
    mapping: dict[str, tuple[str, ...]] = {}
    for entry in (settings.RAG_ROLE_KEYS or "").split(";"):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        token, _, raw_roles = entry.partition("=")
        token = token.strip()
        roles = parse_role_list(raw_roles)
        if token and roles:
            mapping[token] = roles
    return mapping


def apply_demo_role(token_roles: tuple[str, ...], demo_role: str | None) -> tuple[str, ...]:
    """Use a sidebar role only while the API is open and demo mode is on.

    A configured API key or role token keeps its own roles. An unknown demo
    role is ignored.
    """
    if settings.API_KEY or role_keys() or not settings.RBAC_DEMO or not demo_role:
        return token_roles
    allowed = parse_role_list(settings.RBAC_DEMO_ROLES) or ("employee", "hr", "manager")
    chosen = demo_role.strip().lower()
    if chosen in allowed:
        return (chosen,)
    return token_roles


def resolve_roles(token: str | None) -> tuple[str, ...] | None:
    """Roles for this bearer token.

    Returns None when a key is required and the token does not match.
    When no keys are configured, returns the default open-mode roles.
    """
    keys = role_keys()
    if not settings.API_KEY and not keys:
        return parse_role_list(settings.DEFAULT_ACCESS_ROLES) or ("employee",)
    if token and token in keys:
        return keys[token]
    if token and settings.API_KEY and token == settings.API_KEY:
        return parse_role_list(settings.RAG_API_KEY_ROLES) or (ADMIN_ROLE,)
    return None


def load_policy(path: str | None = None) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    """Return ``(default_roles, rules)``. A missing file allows the default roles."""
    policy_path = path if path is not None else settings.ACCESS_POLICY_PATH
    default = parse_role_list(settings.DEFAULT_DOCUMENT_ROLES) or ("employee",)
    if not policy_path or not os.path.isfile(policy_path):
        return default, ()

    with open(policy_path, encoding="utf-8") as handle:
        data = json.load(handle)

    file_default = parse_role_list(",".join(data.get("default_roles") or [])) or default
    rules: list[tuple[str, tuple[str, ...]]] = []
    for rule in data.get("rules") or []:
        pattern = str(rule.get("match") or "").strip()
        roles = parse_role_list(",".join(rule.get("roles") or []))
        if pattern and roles:
            rules.append((pattern, roles))
    return file_default, tuple(rules)


def roles_for_document(source_type: str, relative_path: str, path: str | None = None) -> list[str]:
    """Roles allowed to read this file. The last matching rule wins."""
    default_roles, rules = load_policy(path)
    key = document_key(source_type, relative_path)
    chosen = default_roles
    for pattern, roles in rules:
        if fnmatch(key, pattern) or fnmatch(relative_path, pattern):
            chosen = roles
    return list(chosen)


def access_filter(roles: list[str] | tuple[str, ...]):
    """Qdrant filter that keeps chunks this caller may read.

    An admin role (``*``) returns None so the search is not filtered.
    A caller with no roles matches nothing.
    """
    if ADMIN_ROLE in roles:
        return None
    allowed = [role for role in roles if role and role != ADMIN_ROLE]
    if not allowed:
        allowed = [_DENY_ROLE]
    should: list = [
        models.FieldCondition(
            key="access_roles",
            match=models.MatchAny(any=allowed),
        )
    ]
    # Chunks ingested before roles existed have no access_roles field.
    # They stay visible to the default document roles until the next ingest.
    defaults = set(parse_role_list(settings.DEFAULT_DOCUMENT_ROLES) or ("employee",))
    if defaults.intersection(allowed):
        should.append(
            models.IsEmptyCondition(is_empty=models.PayloadField(key="access_roles"))
        )
    return models.Filter(should=should)

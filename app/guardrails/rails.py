import re

from nemoguardrails import LLMRails, RailsConfig

from app.gateway import get_langchain_llm, portkey_client, portkey_model
from app.guardrails.colang_rules import (
    COLANG_CONTENT,
    JAILBREAK_REFUSAL,
    OFF_TOPIC_REFUSAL,
    RAIL_INDICATORS,
    TOPIC_CHECK_PROMPT,
    YAML_CONTENT,
)
from app.observe import info, span, warning

_LABEL_PATTERN = re.compile(r"\b(ON[_ -]?TOPIC|OFF[_ -]?TOPIC|JAILBREAK)\b", re.IGNORECASE)
_DECISION_PATTERN = re.compile(
    r"LABEL:\s*(ON[_ -]?TOPIC|OFF[_ -]?TOPIC|JAILBREAK)\b",
    re.IGNORECASE,
)

_rails: LLMRails | None = None

# A role word after "you are" is a persona only when it is not an ordinary verb.
_ROLE_STOP = {
    "able",
    "allowed",
    "also",
    "asking",
    "creating",
    "deploying",
    "doing",
    "expected",
    "getting",
    "going",
    "looking",
    "making",
    "meant",
    "monitoring",
    "not",
    "only",
    "required",
    "running",
    "saying",
    "seeing",
    "starting",
    "still",
    "supposed",
    "trying",
    "using",
    "working",
}
_PERSONA = re.compile(
    r"\b(?:act as|pretend to be|pretend you(?:'re| are)|from now on you|"
    r"ignore your (?:role|instructions|rules|guidelines)|disregard your)\b"
    r"|\byou(?:'re| are)(?:\s+now)?\s+(?:an?\s+)?(?P<role>[a-z]{3,})\b",
    re.IGNORECASE,
)
_SIDE_REQUEST = re.compile(
    r"\b(?:tell me a joke|tell a joke|joke first|a joke|write (?:me )?a poem|a poem|a riddle|a song|a limerick)\b",
    re.IGNORECASE,
)
_COMPLIED = re.compile(
    r"\b(?:here(?:'s| is) a joke|as a comedian|as a poet|as a pirate|knock knock|once upon a time)\b",
    re.IGNORECASE,
)


def initialize_rails() -> None:
    """
    Build the NeMo LLMRails singleton at app startup.
    Uses the same Groq model as the RAG agent, through Portkey.
    """
    global _rails

    guard_llm = get_langchain_llm(feature="guardrails")

    config = RailsConfig.from_content(colang_content=COLANG_CONTENT, yaml_content=YAML_CONTENT)

    _rails = LLMRails(config, llm=guard_llm)
    info(f"🛡️ NeMo Guardrails initialised ({portkey_model()}).")


def _normalize_label(raw: str) -> str:
    token = raw.upper().replace("-", " ").replace("_", " ")
    token = "_".join(token.split())
    if token.startswith("OFF"):
        return "OFF_TOPIC"
    if token.startswith("ON"):
        return "ON_TOPIC"
    return "JAILBREAK"


def _parse_label(text: str) -> str | None:
    """Read the last LABEL line. Ignore a line that names more than one label."""
    decisions = list(_DECISION_PATTERN.finditer(text or ""))
    if decisions:
        return _normalize_label(decisions[-1].group(1))

    for line in reversed([line.strip() for line in (text or "").splitlines() if line.strip()]):
        matches = list(_LABEL_PATTERN.finditer(line))
        kinds = {_normalize_label(match.group(1)) for match in matches}
        if len(kinds) == 1:
            return kinds.pop()
    return None


def _topic_label(message: str) -> str | None:
    """Ask for one label. NeMo's example flows miss questions that are not in the list."""
    try:
        response = portkey_client.chat.completions.create(
            model=portkey_model(),
            messages=[{"role": "user", "content": TOPIC_CHECK_PROMPT.format(message=message)}],
            temperature=0,
            max_completion_tokens=200,
            timeout=30,
        )
    except Exception as exc:
        warning(f"Topic check failed: {exc}")
        return None

    choice = response.choices[0].message
    content = choice.content or ""
    reasoning = getattr(choice, "reasoning", None) or getattr(choice, "reasoning_content", None) or ""
    return _parse_label(f"{content}\n{reasoning}")


def mixed_injection(message: str) -> bool:
    """A persona change or an off-topic side request makes the whole message unsafe.

    A Kubernetes question in the same sentence does not cancel it. Typos in the
    role name still match, because the check looks at the instruction shape.
    """
    text = message or ""
    if _SIDE_REQUEST.search(text):
        return True
    match = _PERSONA.search(text)
    if not match:
        return False
    role = (match.groupdict().get("role") or "").lower()
    return not (role and role in _ROLE_STOP)


def answer_complied_with_injection(answer: str) -> bool:
    """The answer adopted the injected role or delivered the off-topic request."""
    return bool(_COMPLIED.search(answer or ""))


def guard(message: str) -> tuple[bool, str | None]:
    """
    Run a user message through the topic check, then the NeMo dialog rails.

    Returns:
        (True,  rail_response) — a rail fired; return this response immediately,
                                skip the RAG pipeline entirely.
        (False, None)          — message is in scope; proceed to LangGraph.
    """
    with span("🛡️ Guardrails Check"):
        if mixed_injection(message):
            info(f"🛡️ Mixed instruction blocked | query='{message[:80]}'")
            return True, JAILBREAK_REFUSAL

        label = _topic_label(message)
        if label == "OFF_TOPIC":
            info(f"🛡️ Off-topic blocked | query='{message[:80]}'")
            return True, OFF_TOPIC_REFUSAL
        if label == "JAILBREAK":
            info(f"🛡️ Jailbreak blocked | query='{message[:80]}'")
            return True, JAILBREAK_REFUSAL

        if _rails is None:
            warning("⚠️ Guardrails not initialised — skipping dialog rails.")
            return False, None

        result = _rails.generate(messages=[{"role": "user", "content": message}])

        # NeMo returns {'role': 'assistant', 'content': '...'} — extract text
        content = result.get("content", "") if isinstance(result, dict) else str(result)

        fired = any(indicator in content for indicator in RAIL_INDICATORS)

        if fired:
            info(f"🛡️ Guardrails fired | query='{message[:80]}'")
            return True, content

        info("✅ Guardrails passed.")
        return False, None

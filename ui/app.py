import os
import time
import uuid

import requests
import streamlit as st
from dotenv import dotenv_values, load_dotenv
from langsmith import trace

# Load environment variables explicitly from the root directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(dotenv_path=env_path)


def _langsmith_status() -> str:
    """Sidebar status for the tracer this UI actually sends runs to."""
    tracing_on = os.getenv("LANGSMITH_TRACING", "").strip().lower() in {"1", "true", "yes"}
    has_key = bool(os.getenv("LANGSMITH_API_KEY", "").strip())
    project = os.getenv("LANGSMITH_PROJECT", "rag_scale_test").strip() or "rag_scale_test"
    if tracing_on and has_key:
        return f"Tracing · {project}"
    if not has_key:
        return "Standby (LANGSMITH_API_KEY not set)"
    return "Standby (LANGSMITH_TRACING is off)"


LANGSMITH_STATUS = _langsmith_status()

DEMO_ROLES = [
    role.strip()
    for role in os.getenv("RBAC_DEMO_ROLES", "employee,hr,manager").split(",")
    if role.strip()
] or ["employee", "hr", "manager"]

ROLE_NOTES = {
    "employee": "You can read the shared documents. Restricted files stay hidden.",
    "hr": "You can read the shared documents, plus anything in a restricted folder.",
    "manager": "You can read the shared documents, plus anything in a restricted folder.",
}

SUGGESTIONS = [
    "How do the cron jobs run?",
    "How does pod autoscaling work?",
    "How do I monitor a job?",
]

AUDIENCE_LABELS = {
    "Public": "public",
    "Employee": "employee",
    "HR": "hr",
    "Manager": "manager",
}


MANAGER_USERNAME = "admin"
MANAGER_PASSWORD = "admin"


def _env_flag(name: str) -> bool:
    """True when `.env` currently sets the named flag to true/1/yes.

    Read the file on every run. `load_dotenv` does not replace a value the
    process already loaded, so editing `.env` would otherwise leave the
    UI out of date.
    """
    raw = dotenv_values(env_path).get(name)
    return str(raw or "").strip().lower() in {"1", "true", "yes"}


def _document_admin_enabled() -> bool:
    return _env_flag("DOCUMENT_ADMIN_ENABLED")


def _backend_url() -> str:
    return os.getenv("BACKEND_URL", "http://localhost:8000")


def _evals_url() -> str:
    """Evals UI URL. Prefer EVALS_URL; otherwise /evals/ behind nginx."""
    configured = os.getenv("EVALS_URL", "").strip()
    if configured:
        return configured if configured.endswith("/") else f"{configured}/"
    return "/evals/"



def _reset_manager_auth() -> None:
    st.session_state.manager_authenticated = False


def _on_demo_role_change() -> None:
    _reset_chat()
    if st.session_state.get("demo_role") != "manager":
        _reset_manager_auth()



def _api_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.getenv('RAG_API_KEY', '')}"}


def _document_error(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    return f"The request failed ({response.status_code})."


def _reset_chat() -> None:
    """Drop the visible chat and the memory thread so a new role cannot see old answers."""
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())


def _role_note(role: str) -> str:
    return ROLE_NOTES.get(role, "Answers use only the documents this role is allowed to read.")


def _load_documents(active_role: str) -> list[dict]:
    response = requests.get(
        f"{_backend_url()}/documents",
        params={"demo_role": active_role},
        headers=_api_headers(),
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(_document_error(response))
    return list(response.json().get("documents") or [])


def _render_document_admin(active_role: str) -> None:
    """Upload and delete. Only a manager sees this, and the buttons work only while the flag is on."""
    if active_role != "manager":
        return
    enabled = _document_admin_enabled()
    st.markdown("**Documents**")
    if not enabled:
        st.caption("Adding and removing files is switched off for now.")
    else:
        st.caption("Add a file and choose who should be able to read it. The same contents are kept only once.")
    with st.form("add-document", clear_on_submit=True):
        uploaded = st.file_uploader(
            "File",
            type=["pdf", "txt", "html", "htm", "docx", "pptx"],
            disabled=not enabled,
        )
        audience_label = st.selectbox("Who can read this", list(AUDIENCE_LABELS), disabled=not enabled)
        st.caption("Public is open to every role. Employee, HR, and Manager keep the file to that role.")
        submitted = st.form_submit_button("Add document", disabled=not enabled, width="stretch")
    if enabled and submitted:
        if uploaded is None:
            st.warning("Choose a file first.")
        else:
            try:
                response = requests.post(
                    f"{_backend_url()}/documents",
                    data={"audience": AUDIENCE_LABELS[audience_label], "demo_role": active_role},
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                    headers=_api_headers(),
                    timeout=180,
                )
            except requests.RequestException as exc:
                st.error(f"The upload did not finish: {exc}")
            else:
                if response.status_code == 200:
                    st.session_state.document_notice = f"Added {uploaded.name} for {audience_label.lower()}."
                    st.rerun()
                elif response.status_code == 409:
                    st.warning(_document_error(response))
                else:
                    st.error(_document_error(response))

    notice = st.session_state.get("document_notice")
    if notice:
        st.success(notice)
        st.session_state.document_notice = None

    try:
        documents = _load_documents(active_role)
    except Exception as exc:
        st.error(str(exc))
        return
    if not documents:
        st.caption("No documents are indexed yet.")
        return
    st.caption(f"{len(documents)} indexed")
    for index, document in enumerate(documents):
        name_col, role_col, delete_col = st.columns([3, 1, 1])
        name_col.write(document["relative_path"])
        role_col.write(document.get("audience") or "employee")
        if delete_col.button("Delete", key=f"delete-doc-{index}", disabled=not enabled, width="stretch"):
            try:
                response = requests.delete(
                    f"{_backend_url()}/documents",
                    json={
                        "relative_path": document["relative_path"],
                        "source_type": document.get("source_type") or "general",
                        "demo_role": active_role,
                    },
                    headers=_api_headers(),
                    timeout=60,
                )
            except requests.RequestException as exc:
                st.error(f"The delete did not finish: {exc}")
            else:
                if response.status_code == 200:
                    st.session_state.document_notice = f"Deleted {document['relative_path']}."
                    st.rerun()
                else:
                    st.error(_document_error(response))


def _present_source(raw: str) -> tuple[str, str]:
    """Turn a retrieval string into a short title and the passage text."""
    text = raw.strip()
    if text.upper().startswith("CONTENT:"):
        text = text.split(":", 1)[1].strip()
    if text.startswith("[") and "]" in text:
        header, _, body = text.partition("]")
        title = header[1:].strip() or "Source"
        return title, body.strip() or text
    return "Source", text


def _show_steps(steps: list[str]) -> None:
    if not steps:
        return
    with st.expander("Retrieval steps"):
        st.caption("Here is how the assistant found the passages and wrote the answer.")
        for step in steps:
            st.write(step)


def _show_sources(sources: list[str]) -> None:
    if not sources:
        return
    with st.expander(f"Sources used · {len(sources)}"):
        st.caption("These are the passages this role is allowed to read.")
        for source in sources:
            title, body = _present_source(source)
            st.markdown(f"**{title}**")
            st.write(body)


def _show_message(message: dict) -> None:
    avatar = "🤖" if message["role"] == "assistant" else "👤"
    with st.chat_message(message["role"], avatar=avatar):
        if message["role"] == "assistant":
            label = message.get("access_role", "employee")
            mode = "Normal demo" if message.get("demo_mode") == "normal" else "RBAC demo"
            st.markdown(
                f"<span class='role-pill'>{mode} · {label}</span>",
                unsafe_allow_html=True,
            )
        if message.get("blocked"):
            st.caption("Stopped by the guardrails. No answer was written from the documents.")
        st.markdown(message["content"])
        _show_steps(message.get("steps") or [])
        _show_sources(message.get("sources") or [])


# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Policy assistant",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ink: #1c1915;
        --muted: #3f3a34;
        --line: #ddd4c6;
        --paper: #f6f3ec;
        --surface: #fffdf9;
        --accent: #267a4e;
        --accent-soft: #e7f6ee;
    }
    .stApp, [data-testid="stMarkdown"], [data-testid="stCaptionContainer"] {
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        color: var(--ink);
    }
    .stButton button { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; }
    .stApp { background: var(--paper); font-size: 1rem; line-height: 1.5; }
    [data-testid="stSidebar"] {
        background: var(--surface);
        border-right: 1px solid var(--line);
        color: var(--ink);
    }
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label { color: var(--ink); font-size: 0.9375rem; line-height: 1.45; }
    [data-testid="stCaptionContainer"] { color: var(--muted); font-size: 0.875rem; line-height: 1.45; }
    button[kind="secondary"] p,
    button[kind="primary"] p { white-space: normal; text-align: left; font-size: 1rem; line-height: 1.4; }
    .block-container { padding-top: 1.5rem; max-width: 42rem; }
    .sidebar-title {
        margin: 0 0 0.25rem;
        color: var(--ink);
        font-size: 1.25rem;
        font-weight: 600;
        letter-spacing: -0.02em;
        line-height: 1.3;
    }
    .eyebrow {
        margin: 0;
        color: var(--muted);
        font-size: 0.875rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    h1.app-title {
        margin: 0.2rem 0 0.4rem;
        color: var(--ink);
        font-size: 1.75rem !important;
        font-weight: 600 !important;
        line-height: 1.25 !important;
        letter-spacing: -0.02em;
    }
    .lede { color: var(--ink); margin: 0 0 0.85rem; font-size: 1.0625rem; line-height: 1.55; }
    .capabilities {
        margin: 0 0 1.1rem;
        padding: 0;
        list-style: none;
        display: grid;
        gap: 0.5rem;
    }
    .capabilities li {
        margin: 0;
        padding: 0.65rem 0.8rem;
        background: var(--surface);
        border: 1px solid var(--line);
        border-left: 3px solid var(--accent);
        border-radius: 8px;
        color: var(--ink);
        font-size: 1rem;
        line-height: 1.5;
    }
    .capabilities strong { color: var(--accent); font-weight: 600; }
    .section-label {
        margin: 0.15rem 0 0.35rem;
        color: var(--ink);
        font-size: 1rem;
        font-weight: 600;
        line-height: 1.4;
    }
    .try-path {
        margin: 0 0 0.85rem;
        padding-left: 1.25rem;
        color: var(--ink);
        font-size: 1rem;
        line-height: 1.5;
    }
    .try-path li { margin: 0.35rem 0; }
    .role-pill {
        display: inline-block;
        margin-bottom: 0.45rem;
        padding: 0.15rem 0.55rem;
        border: 1px solid #b7dcc6;
        border-radius: 999px;
        color: var(--accent);
        background: var(--accent-soft);
        font-size: 0.875rem;
        font-weight: 600;
        line-height: 1.4;
    }
    [data-testid="stChatMessage"] { background: transparent; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- SESSION ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "demo_mode" not in st.session_state:
    st.session_state.demo_mode = "normal"
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None
if "manager_authenticated" not in st.session_state:
    st.session_state.manager_authenticated = False

# --- SIDEBAR ---
with st.sidebar:
    st.markdown('<p class="sidebar-title">Policy assistant</p>', unsafe_allow_html=True)
    st.caption("Ask in plain language. You only see the documents this role can read.")

    normal_type = "primary" if st.session_state.demo_mode == "normal" else "secondary"
    rbac_type = "primary" if st.session_state.demo_mode == "rbac" else "secondary"
    if st.button("Normal demo", width="stretch", type=normal_type):
        if st.session_state.demo_mode != "normal":
            st.session_state.demo_mode = "normal"
            _reset_manager_auth()
            _reset_chat()
            st.rerun()
    if st.button("RBAC demo", width="stretch", type=rbac_type):
        if st.session_state.demo_mode != "rbac":
            st.session_state.demo_mode = "rbac"
            _reset_manager_auth()
            _reset_chat()
            st.rerun()

    manager_locked = False
    if st.session_state.demo_mode == "normal":
        active_role = "employee"
        st.markdown("**You're an employee**")
        st.caption("One role for this demo. " + _role_note("employee"))
    else:
        if "demo_role" not in st.session_state or st.session_state.demo_role not in DEMO_ROLES:
            st.session_state.demo_role = "employee" if "employee" in DEMO_ROLES else DEMO_ROLES[0]
        st.selectbox("Answer as", DEMO_ROLES, key="demo_role", on_change=_on_demo_role_change)
        active_role = st.session_state.demo_role
        st.caption(_role_note(active_role) + " Switching role starts a fresh conversation.")

        if active_role == "manager" and not st.session_state.manager_authenticated:
            manager_locked = True
            st.markdown("**Manager sign-in**")
            st.caption("Username and password are both `admin` for this demo.")
            with st.form("manager-login"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
            if submitted:
                if username.strip() == MANAGER_USERNAME and password == MANAGER_PASSWORD:
                    st.session_state.manager_authenticated = True
                    st.rerun()
                else:
                    st.error("Wrong username or password.")
        elif active_role == "manager":
            st.success("Signed in as manager.")
            st.link_button("Open evals", _evals_url(), type="primary", width="stretch")
            if st.button("Sign out manager", width="stretch"):
                _reset_manager_auth()
                _reset_chat()
                st.rerun()

    if (
        st.session_state.demo_mode == "rbac"
        and active_role == "manager"
        and st.session_state.manager_authenticated
    ):
        with st.expander("Manage documents"):
            _render_document_admin(active_role)

    if st.button("New conversation", width="stretch"):
        _reset_chat()
        st.rerun()

    with st.expander("Connection details"):
        st.write(LANGSMITH_STATUS)
        st.caption(f"Memory {st.session_state.session_id[:8]}")


# --- HEADER ---
mode_label = "Normal demo" if st.session_state.demo_mode == "normal" else "RBAC demo"
st.markdown(
    f"""
    <p class="eyebrow">{mode_label}</p>
    <h1 class="app-title">Policy assistant</h1>
    <p class="lede">Ask the way you would ask a teammate. The answer comes from the company documents, and you're asking as <strong>{active_role}</strong>. {_role_note(active_role)}</p>
    <ul class="capabilities">
        <li><strong>Answers from your documents.</strong> Every reply is built from passages the assistant actually found.</li>
        <li><strong>Your role changes the view.</strong> Employee, HR, and manager do not all see the same files.</li>
        <li><strong>Unsafe questions stop here.</strong> Guardrails block them before an answer is written.</li>
        <li><strong>Managers look after the library.</strong> They can add or remove files, and only when that switch is on.</li>
    </ul>
    """,
    unsafe_allow_html=True,
)

pending = st.session_state.pending_prompt
if manager_locked:
    st.info("Sign in as manager in the sidebar (username `admin`, password `admin`) to continue.")
elif not st.session_state.messages and not pending:
    if _document_admin_enabled():
        manage_step = "Sign in as manager with admin / admin, then open Manage documents and choose who should read the file."
    else:
        manage_step = "Sign in as manager with admin / admin. Adding and removing files is switched off for now."
    st.markdown(
        f"""
        <p class="section-label">See it in three steps</p>
        <ol class="try-path">
            <li>Start as an employee and try one of the questions below.</li>
            <li>Open RBAC demo and switch to HR or manager. The chat clears, and the next answer follows that role.</li>
            <li>{manage_step}</li>
        </ol>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Or start with one of these.")
    for index, suggestion in enumerate(SUGGESTIONS):
        if st.button(suggestion, key=f"suggest-{index}", width="stretch"):
            st.session_state.pending_prompt = suggestion
            st.rerun()

for message in st.session_state.messages:
    _show_message(message)

if manager_locked:
    typed = None
    prompt = None
else:
    typed = st.chat_input("Ask how a job runs, how pods scale, or how to monitor work...")
    prompt = typed or pending

if prompt:
    st.session_state.pending_prompt = None
    st.session_state.messages.append({"role": "user", "content": prompt})
    _show_message(st.session_state.messages[-1])

    with st.chat_message("assistant", avatar="🤖"):
        with st.status("Reading the documents you can see...", expanded=False) as status:
            data: dict = {}
            full_answer = "No response."
            try:
                with trace(
                    "ui_chat",
                    inputs={"question": prompt},
                    metadata={"session_id": st.session_state.session_id, "demo_role": active_role},
                ) as run:
                    base_url = os.getenv("BACKEND_URL", "http://localhost:8000")
                    url = f"{base_url}/query"
                    payload = {
                        "q": prompt,
                        "thread_id": f"{st.session_state.session_id}:{active_role}",
                        "demo_role": active_role,
                    }
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('RAG_API_KEY', '')}",
                    }
                    headers.update(run.to_headers())
                    response = requests.post(url, json=payload, headers=headers, timeout=180)
                    data = response.json()
                    run.end(outputs={"status": data.get("status"), "answer": data.get("answer")})

                if data.get("status") == "Blocked by guardrails.":
                    status.update(label="Blocked by guardrails", state="complete", expanded=False)
                    full_answer = data.get("answer", "Blocked by guardrails.")
                elif "answer" in data:
                    status.update(label="Answer ready", state="complete", expanded=False)
                    full_answer = data.get("answer", "No response.")
                elif "job_id" in data:
                    job_id = data["job_id"]
                    poll_url = f"{base_url}/query/status/{job_id}"
                    result_data = None
                    max_attempts = 60
                    for attempt in range(max_attempts):
                        poll_resp = requests.get(poll_url, headers=headers, timeout=30)
                        poll_resp.raise_for_status()
                        poll_data = poll_resp.json()
                        job_status = poll_data.get("status", "UNKNOWN")
                        status.write(f"Job status: {job_status} ({attempt + 1}/{max_attempts})")
                        if job_status in ("SUCCESS", "FAILURE"):
                            result_data = poll_data.get("result") or poll_data.get("error")
                            break
                        time.sleep(2)
                    if result_data is None:
                        raise RuntimeError("Polling timed out waiting for the RAG job to complete.")
                    if isinstance(result_data, dict):
                        data = result_data
                        status.update(label="Answer ready", state="complete", expanded=False)
                        full_answer = data.get("answer", "No response.")
                    else:
                        raise RuntimeError(f"RAG job failed: {result_data}")
                else:
                    raise RuntimeError(f"Unexpected /query response: {data}")
            except Exception as exc:
                status.update(label="Could not reach the assistant", state="error")
                st.error(f"The request did not finish: {exc}")
                st.stop()

        blocked = data.get("status") == "Blocked by guardrails."
        st.markdown(
            f"<span class='role-pill'>{mode_label} · {active_role}</span>",
            unsafe_allow_html=True,
        )
        if blocked:
            st.caption("Stopped by the guardrails. No answer was written from the documents.")
        answer_placeholder = st.empty()
        shown = ""
        words = full_answer.split(" ")
        for index, word in enumerate(words):
            shown += word if index == 0 else f" {word}"
            answer_placeholder.markdown(shown + " ▌")
            time.sleep(0.012)
        answer_placeholder.markdown(full_answer)

        steps = data.get("thought_process") or []
        sources = data.get("sources") or []
        _show_steps(steps)
        _show_sources(sources)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": full_answer,
            "access_role": active_role,
            "demo_mode": st.session_state.demo_mode,
            "blocked": data.get("status") == "Blocked by guardrails.",
            "steps": data.get("thought_process") or [],
            "sources": data.get("sources") or [],
        }
    )

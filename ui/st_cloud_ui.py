import os
import time
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv
from langsmith import trace

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))


def _langsmith_status() -> str:
    """Sidebar status for the tracer this UI sends runs to."""
    tracing_on = os.getenv("LANGSMITH_TRACING", "").strip().lower() in {"1", "true", "yes"}
    has_key = bool(os.getenv("LANGSMITH_API_KEY", "").strip())
    project = os.getenv("LANGSMITH_PROJECT", "rag_scale_test").strip() or "rag_scale_test"
    if tracing_on and has_key:
        return f"Tracing · {project}"
    if not has_key:
        return "Standby (LANGSMITH_API_KEY not set)"
    return "Standby (LANGSMITH_TRACING is off)"


LANGSMITH_STATUS = _langsmith_status()

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Enterprise Agentic RAG",
    page_icon="🤖",
    layout="wide",
)

# --- AVATARS ---
AI_AVATAR = "🤖"
USER_AVATAR = "👤"

# --- SESSION MANAGEMENT ---
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

# --- SIDEBAR ---
with st.sidebar:
    st.title("🧠 Agent OS")
    st.markdown("---")

    base_url = "http://localhost:8000"

    st.markdown("---")
    st.success(f"LangSmith: {LANGSMITH_STATUS}")
    st.info(f"Memory ID: {st.session_state.session_id[:8]}")

    if st.button("🗑️ Clear History & Memory", width="stretch", type="primary"):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

# --- MAIN CHAT ---
st.title("🤖 Enterprise Agentic Assistant")

# Display history
for message in st.session_state.messages:
    avatar = AI_AVATAR if message["role"] == "assistant" else USER_AVATAR
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])

# Chat Input
if prompt := st.chat_input("Ask about your documentation..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar=AI_AVATAR):
        data = {}
        with st.status("🔍 Agent is thinking...", expanded=True) as status:
            try:
                with trace(
                    "ui_chat",
                    inputs={"question": prompt},
                    metadata={"session_id": st.session_state.session_id},
                ) as run:
                    url = f"{base_url}/query"
                    payload = {"q": prompt, "thread_id": st.session_state.session_id}
                    headers = {"Content-Type": "application/json"}
                    headers.update(run.to_headers())
                    response = requests.post(url, json=payload, headers=headers, timeout=60)

                    if response.status_code != 200:
                        run.end(error=response.text)
                        st.error(f"Backend Error: {response.status_code} - {response.text}")
                        st.stop()

                    data = response.json()
                    run.end(outputs={"status": data.get("status"), "answer": data.get("answer")})

                steps = data.get("thought_process", [])
                for step in steps:
                    st.markdown(f"⚙️ {step}", unsafe_allow_html=False)

                status.update(label="✅ Answer Synthesized", state="complete", expanded=False)

            except Exception:
                status.update(label="❌ Connection Failed", state="error")
                st.error("Backend Offline.")
                st.stop()

        answer_placeholder = st.empty()
        full_answer = data.get("answer", "No response.")

        curr_text = ""
        for char in full_answer:
            curr_text += char
            answer_placeholder.markdown(curr_text + "▌")
            time.sleep(0.005)
        answer_placeholder.markdown(full_answer)

        sources = data.get("sources", [])
        if sources:
            with st.expander(f"📄 Retrieved Context ({len(sources)} chunks)"):
                for i, source in enumerate(sources):
                    st.caption(f"Chunk {i + 1}")
                    st.info(source)
        else:
            st.caption("ℹ️ No context retrieved — conversational response.")

        st.session_state.messages.append({"role": "assistant", "content": full_answer})

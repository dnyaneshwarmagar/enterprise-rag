"""Centralized, Pydantic-validated application settings."""

import os
from urllib.parse import quote, urlunsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load and validate environment variables from `.env`.

    - `extra="ignore"` lets `.env` keep legacy keys (`POSTGRES_URI`, `REDIS_URL`,
      old Groq keys, etc.) without failing startup.
    - Required fields raise a clear validation error at import time if missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- JINA AI (embeddings + reranker) ---
    JINA_API_KEY: str

    # --- OPENAI LLM ---
    OPENAI_API_KEY: str
    JUDGE_OPENAI_API_KEY: str | None = None

    # --- GROQ (LiteLLM proxy + optional direct judge) ---
    GROQ_API_KEY: str | None = None
    # Optional second Groq key for LiteLLM fallback routing.
    GROQ_FALLBACK_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    # RAGAS judge prefers this direct key (bypasses the proxy).
    JUDGE_GROQ_API_KEY: str | None = None
    JUDGE_GROQ_MODEL: str = "openai/gpt-oss-20b"

    # --- LITELLM PROXY (default LLM gateway; Portkey replacement) ---
    # litellm | portkey — portkey kept for rollback only.
    LLM_GATEWAY: str = "litellm"
    LITELLM_BASE_URL: str = "http://127.0.0.1:4000"
    LITELLM_MASTER_KEY: str = "sk-litellm-local"
    # Aliases defined in deploy/litellm_config.yaml
    LITELLM_MODEL: str = "rag-primary"
    LITELLM_FALLBACK_MODEL: str = "rag-fallback"

    # --- PORTKEY LLM GATEWAY (optional rollback when LLM_GATEWAY=portkey) ---
    PORTKEY_API_KEY: str | None = None
    PORTKEY_PRIMARY_SLUG: str = "hrpolicyrag"
    PORTKEY_FALLBACK_SLUG: str = "hrpolicybackup"
    PORTKEY_MODEL: str = "openai/gpt-oss-20b"
    PORTKEY_PRIMARY_CONFIG_ID: str | None = None

    # --- S3 DOCUMENT STORAGE ---
    # When set, ingestion uploads source files and parsed JSON here.
    # Source listing can also be an s3://bucket/prefix path.
    S3_BUCKET: str | None = None
    S3_PREFIX: str = "documents"
    S3_PROCESSED_PREFIX: str = "processed"
    AWS_REGION: str | None = None

    # --- QDRANT VECTOR DB ---
    QDRANT_URL: str = Field(validation_alias=AliasChoices("QDRANT_URL", "QDRANT_CLUSTER_ENDPOINT"))
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "enterprise_rag"

    # --- NEON SERVERLESS POSTGRES (LangGraph checkpointer) ---
    NEON_DB_URL: str

    # --- UPSTASH REDIS (rate limiting) ---
    UPSTASH_REDIS_REST_URL: str
    UPSTASH_REDIS_REST_TOKEN: str

    # --- API SAFETY ---
    API_KEY: str | None = Field(default=None, alias="RAG_API_KEY")
    # Roles granted to RAG_API_KEY. "*" can read every document.
    RAG_API_KEY_ROLES: str = "*"
    # Limited tokens, as token=role,role;token2=hr. The client cannot send roles.
    RAG_ROLE_KEYS: str = ""
    # Used only when no API key is configured.
    DEFAULT_ACCESS_ROLES: str = "employee"
    # Roles written on a document when access_policy.json has no matching rule.
    DEFAULT_DOCUMENT_ROLES: str = "employee"
    ACCESS_POLICY_PATH: str = "access_policy.json"
    # Local UI can ask to answer as one of these roles. Ignored when an API key is set.
    RBAC_DEMO: bool = True
    RBAC_DEMO_ROLES: str = "employee,hr,manager"
    # Manager upload and delete. False disables the buttons and rejects the API.
    DOCUMENT_ADMIN_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 20
    STRICT_STARTUP: bool = False

    # --- OBSERVABILITY ---
    LANGSMITH_TRACING: str = "true"
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "rag_scale_test"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    @field_validator(
        "QDRANT_API_KEY",
        "PORTKEY_PRIMARY_CONFIG_ID",
        "PORTKEY_API_KEY",
        "S3_BUCKET",
        "AWS_REGION",
        "JUDGE_GROQ_API_KEY",
        "JUDGE_OPENAI_API_KEY",
        "GROQ_API_KEY",
        "GROQ_FALLBACK_API_KEY",
        mode="before",
    )
    @classmethod
    def _empty_optional_as_none(cls, v):
        """Treat blank optional secrets as unset so they are not sent as empty headers."""
        if v == "" or v is None:
            return None
        return v

    @property
    def groq_api_key(self) -> str | None:
        """Groq key for LiteLLM; falls back to the dedicated judge key."""
        return self.GROQ_API_KEY or self.JUDGE_GROQ_API_KEY

    @property
    def groq_fallback_api_key(self) -> str | None:
        """Fallback Groq key for LiteLLM routing."""
        return self.GROQ_FALLBACK_API_KEY or self.groq_api_key

    @property
    def judge_groq_api_key(self) -> str | None:
        """Direct Groq key for RAGAS judge, if configured."""
        return self.JUDGE_GROQ_API_KEY or self.GROQ_API_KEY

    @property
    def use_litellm_gateway(self) -> bool:
        return (self.LLM_GATEWAY or "litellm").strip().lower() == "litellm"

    @property
    def judge_api_key(self) -> str:
        """Dedicated judge key, falling back to the main OpenAI key."""
        return self.JUDGE_OPENAI_API_KEY or self.OPENAI_API_KEY

    @property
    def postgres_uri(self) -> str:
        """LangGraph Postgres checkpointer URI (Neon).

        Serverless Postgres closes idle connections, so append TCP keepalive
        options to keep the connection pool healthy between requests.
        """
        base = self.NEON_DB_URL.rstrip("/")
        keepalive = "keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
        if "?" in base:
            return f"{base}&{keepalive}"
        return f"{base}?{keepalive}"

    @property
    def redis_url(self) -> str:
        """TLS Redis URL derived from Upstash REST credentials.

        Upstash exposes the same host for REST and TLS Redis. The REST token is
        used as the Redis password under the default username. The result is
        passed to `limits` for rate limiting and to the health checker.
        """
        host = self.UPSTASH_REDIS_REST_URL.replace("https://", "").rstrip("/")
        token = quote(self.UPSTASH_REDIS_REST_TOKEN, safe="")
        netloc = f"default:{token}@{host}"
        return urlunsplit(("rediss", netloc, "/0", "ssl_cert_reqs=required", ""))


# Singleton used across the app.
settings = Settings()


def _document_admin_raw() -> str | None:
    """Current DOCUMENT_ADMIN_ENABLED line in `.env`, if the file sets one."""
    from dotenv import dotenv_values

    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.isfile(path):
        return None
    raw = dotenv_values(path).get("DOCUMENT_ADMIN_ENABLED")
    if raw is None:
        return None
    return str(raw)


def document_admin_enabled() -> bool:
    """Whether a manager may upload or delete documents right now.

    The value is read from `.env` on every call. The copy stored on `settings`
    is filled once at startup, and an old process environment must not keep
    upload and delete available after the file is changed to false.
    """
    raw = _document_admin_raw()
    if raw is None or raw.strip() == "":
        return bool(settings.DOCUMENT_ADMIN_ENABLED)
    return raw.strip().lower() in {"1", "true", "yes"}


def apply_langchain_env():
    """Write LangSmith/LangChain settings to os.environ for automatic tracing.

    Tracing is only activated when both LANGSMITH_TRACING and LANGSMITH_API_KEY
    are set — enabling tracing without a key causes LangChain to emit 401 noise
    on every LangGraph step.
    """
    if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.LANGSMITH_TRACING)
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    if settings.LANGSMITH_PROJECT:
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
    if settings.LANGSMITH_ENDPOINT:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", settings.LANGSMITH_ENDPOINT)


apply_langchain_env()

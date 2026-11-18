#!/bin/sh
# Normalize Groq keys for the LiteLLM proxy, then start it.
set -eu

# Prefer dedicated GROQ_API_KEY; fall back to JUDGE_GROQ_API_KEY so one Groq
# key is enough for local/dev.
if [ -z "${GROQ_API_KEY:-}" ] && [ -n "${JUDGE_GROQ_API_KEY:-}" ]; then
  export GROQ_API_KEY="$JUDGE_GROQ_API_KEY"
fi

if [ -z "${GROQ_FALLBACK_API_KEY:-}" ]; then
  export GROQ_FALLBACK_API_KEY="${GROQ_API_KEY:-}"
fi

if [ -z "${GROQ_API_KEY:-}" ]; then
  echo "ERROR: GROQ_API_KEY (or JUDGE_GROQ_API_KEY) must be set for LiteLLM." >&2
  exit 1
fi

# Neon/Postgres URLs from the app .env must not reach LiteLLM (Prisma).
# Compose may inject empty strings; treat those as unset.
if [ -z "${DATABASE_URL:-}" ]; then
  unset DATABASE_URL || true
fi
if [ -z "${DIRECT_URL:-}" ]; then
  unset DIRECT_URL || true
fi
unset DATABASE_URL_UNPOOLED || true

export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-litellm-local}"
export DISABLE_SCHEMA_UPDATE="${DISABLE_SCHEMA_UPDATE:-true}"
export STORE_MODEL_IN_DB="${STORE_MODEL_IN_DB:-False}"

# App uses Upstash REST Redis; LiteLLM must not treat that as a Redis cache backend.
unset REDIS_URL REDIS_HOST REDIS_PORT REDIS_PASSWORD || true

echo "LiteLLM entrypoint: GROQ_API_KEY set (len=${#GROQ_API_KEY}), starting proxy..."
exec litellm --config /app/deploy/litellm_config.yaml --host 0.0.0.0 --port 4000

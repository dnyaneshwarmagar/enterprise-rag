#!/usr/bin/env bash
# Live smoke: GET /health + POST /query (API → LiteLLM → Groq).
# Usage:
#   SMOKE_BASE_URL=http://127.0.0.1:8000 bash deploy/smoke_groq_query.sh
#   SMOKE_BASE_URL=http://<ec2-ip>/api bash deploy/smoke_groq_query.sh
set -euo pipefail

BASE="${SMOKE_BASE_URL:-http://127.0.0.1:8000}"
BASE="${BASE%/}"
TIMEOUT="${SMOKE_TIMEOUT_S:-120}"
QUESTION="${SMOKE_QUESTION:-What kubectl command lists Kubernetes Jobs?}"

AUTH_HEADER=()
if [[ -n "${RAG_API_KEY:-${SMOKE_API_KEY:-}}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${RAG_API_KEY:-$SMOKE_API_KEY}")
fi

echo "smoke health: ${BASE}/health"
curl -fsS --max-time 20 "${BASE}/health" | grep -q '"status"' 

echo "smoke query: ${QUESTION}"
body=$(jq -n --arg q "$QUESTION" '{q:$q, thread_id:"deploy-groq-smoke"}')
resp=$(curl -fsS --max-time "$TIMEOUT" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER[@]}" \
  -d "$body" \
  "${BASE}/query")

echo "$resp" | jq -e '
  (.answer | type == "string")
  and (.answer | length >= 8)
  and (.status | type == "string")
  and ((.status | ascii_downcase) | contains("fail") | not)
' >/dev/null

echo "SMOKE_OK"
echo "$resp" | jq -r '"status=" + .status + " answer_prefix=" + (.answer[:120])'

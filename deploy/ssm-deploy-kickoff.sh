#!/usr/bin/env bash
# Short-lived SSM entrypoint: refresh repo, start remote-update in background, exit.
# Keeps SSM sessions seconds-long so t3.micro agents do not ipc-timeout mid docker pull.
set -euo pipefail

STATUS_FILE="${DEPLOY_STATUS_FILE:-/tmp/enterprise-rag-deploy.status}"
LOG_FILE="${DEPLOY_LOG_FILE:-/tmp/enterprise-rag-deploy.log}"
PID_FILE="${DEPLOY_PID_FILE:-/tmp/enterprise-rag-deploy.pid}"
REPO_DIR="${REPO_DIR:-/opt/enterprise-rag}"

cd "$REPO_DIR"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes}"

# Fast sync so kickoff + remote-update.sh match origin/main.
git fetch origin main
git reset --hard origin/main

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${old_pid:-}" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "deploy already running pid=$old_pid"
    exit 1
  fi
fi

echo "running" >"$STATUS_FILE"
: >"$LOG_FILE"

# Background wrapper writes ok/failed even if remote-update exits non-zero.
nohup env \
  ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE:-}" \
  LITELLM_IMAGE="${LITELLM_IMAGE:-}" \
  AWS_REGION="${AWS_REGION:-us-east-1}" \
  GROQ_API_KEY="${GROQ_API_KEY:-}" \
  GROQ_FALLBACK_API_KEY="${GROQ_FALLBACK_API_KEY:-}" \
  JUDGE_GROQ_API_KEY="${JUDGE_GROQ_API_KEY:-}" \
  LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-}" \
  LLM_GATEWAY="${LLM_GATEWAY:-litellm}" \
  LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://litellm:4000}" \
  DEPLOY_STATUS_FILE="$STATUS_FILE" \
  bash -c '
    set +e
    bash /opt/enterprise-rag/deploy/remote-update.sh >>"'"$LOG_FILE"'" 2>&1
    code=$?
    if [ "$code" -eq 0 ]; then
      echo ok >"'"$STATUS_FILE"'"
    else
      echo failed >"'"$STATUS_FILE"'"
    fi
    exit "$code"
  ' >/dev/null 2>&1 &

echo $! >"$PID_FILE"
echo "started pid=$(cat "$PID_FILE") status_file=$STATUS_FILE log=$LOG_FILE"
exit 0

#!/usr/bin/env bash
# Pull main + pull pre-built ECR images (app + evals), then restart Compose.
# Called by GitHub Actions over SSM with ENTERPRISE_RAG_IMAGE / ENTERPRISE_RAG_EVALS_IMAGE set.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REGISTRY="${ECR_REGISTRY:-815398003681.dkr.ecr.us-east-1.amazonaws.com}"

cd /opt/enterprise-rag
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git fetch origin main
# If this script changed on main, re-exec the new file (bash keeps the old inode otherwise).
if ! git diff --quiet HEAD origin/main -- deploy/remote-update.sh; then
  git reset --hard origin/main
  # Preserve CD-injected secrets across re-exec (env replaces the process environ).
  exec env \
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
    bash /opt/enterprise-rag/deploy/remote-update.sh
fi
git reset --hard origin/main

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
fi

# Ensure legacy host services are not binding ports 80/8501/8502.
sudo systemctl disable --now enterprise-rag-api enterprise-rag-ui enterprise-rag-evals nginx 2>/dev/null || true

mkdir -p processed_data
# SSM one-shots may leave .env root-owned; keep it writable by ubuntu.
if [ -e .env ] && [ ! -w .env ]; then
  sudo chown "$(id -u):$(id -g)" .env || true
fi
touch .env
chmod 600 .env

# Upsert a KEY=value in .env without printing secrets.
_upsert_env() {
  local key="$1"
  local value="$2"
  local tmp
  [ -n "$value" ] || return 0
  tmp="$(mktemp)"
  if grep -qE "^${key}=" .env 2>/dev/null; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' .env >"$tmp"
  else
    cat .env >"$tmp"
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  mv "$tmp" .env
  chmod 600 .env
}

# Optional inject from CD (GitHub Secrets → SSM env). Never echo values.
_upsert_env GROQ_API_KEY "${GROQ_API_KEY:-}"
_upsert_env GROQ_FALLBACK_API_KEY "${GROQ_FALLBACK_API_KEY:-}"
_upsert_env JUDGE_GROQ_API_KEY "${JUDGE_GROQ_API_KEY:-}"
_upsert_env LITELLM_MASTER_KEY "${LITELLM_MASTER_KEY:-}"
_upsert_env LLM_GATEWAY "${LLM_GATEWAY:-litellm}"
_upsert_env LITELLM_BASE_URL "${LITELLM_BASE_URL:-http://litellm:4000}"
_upsert_env LITELLM_MODEL "${LITELLM_MODEL:-rag-primary}"
_upsert_env LITELLM_FALLBACK_MODEL "${LITELLM_FALLBACK_MODEL:-rag-fallback}"

# Default images if Actions did not pass them.
export ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-${ECR_REGISTRY}/enterprise-rag:main}"
export ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE:-${ECR_REGISTRY}/enterprise-rag-evals:main}"
export LITELLM_IMAGE="${LITELLM_IMAGE:-${ECR_REGISTRY}/enterprise-rag:litellm-main}"

# ECR login via instance role (enterprise-rag-ec2 / ecr-pull).
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  sudo apt-get update -y
  sudo apt-get install -y unzip
  unzip -q /tmp/awscliv2.zip -d /tmp
  sudo /tmp/aws/install
fi
aws ecr get-login-password --region "${AWS_REGION}" \
  | sudo docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "Pulling ${ENTERPRISE_RAG_IMAGE}"
sudo docker pull "${ENTERPRISE_RAG_IMAGE}"
echo "Pulling ${ENTERPRISE_RAG_EVALS_IMAGE}"
sudo docker pull "${ENTERPRISE_RAG_EVALS_IMAGE}"
echo "Pulling ${LITELLM_IMAGE}"
sudo docker pull "${LITELLM_IMAGE}"

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  LITELLM_IMAGE="${LITELLM_IMAGE}" \
  docker compose pull || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  LITELLM_IMAGE="${LITELLM_IMAGE}" \
  docker compose up -d --remove-orphans --no-build
# Nginx caches upstream IPs; recreate so it sees new api/ui/evals containers.
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  LITELLM_IMAGE="${LITELLM_IMAGE}" \
  docker compose up -d --force-recreate --no-deps nginx
sudo docker image prune -f >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1/api/health >/dev/null; then
    echo
    sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
      ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
      LITELLM_IMAGE="${LITELLM_IMAGE}" \
      docker compose ps
    # Deploy readiness is /api/health only. Live /query smoke runs in CD (single query).
    exit 0
  fi
  sleep 5
done

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  LITELLM_IMAGE="${LITELLM_IMAGE}" \
  docker compose ps || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  LITELLM_IMAGE="${LITELLM_IMAGE}" \
  docker compose logs --tail=80 api evals nginx || true
exit 1

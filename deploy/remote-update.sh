#!/usr/bin/env bash
# Pull main + pull pre-built GHCR image, then restart Compose.
# Called by GitHub Actions over SSM with ENTERPRISE_RAG_IMAGE set.
set -euo pipefail

cd /opt/enterprise-rag
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git fetch origin main
git reset --hard origin/main

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
fi

# Ensure legacy host services are not binding ports 80/8501/8502.
sudo systemctl disable --now enterprise-rag-api enterprise-rag-ui enterprise-rag-evals nginx 2>/dev/null || true

mkdir -p processed_data
chmod 600 .env 2>/dev/null || true

# Default image if Actions did not pass one.
export ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-ghcr.io/dnyaneshwarmagar/enterprise-rag:main}"

# Optional login for private GHCR packages (public packages need no token).
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  # Only load GHCR-related vars; full .env may contain spaces/special chars.
  GHCR_TOKEN="$(grep -E '^GHCR_TOKEN=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  GHCR_USER="$(grep -E '^GHCR_USER=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  set +a
fi
if [[ -n "${GHCR_TOKEN:-}" ]]; then
  echo "${GHCR_TOKEN}" | sudo docker login ghcr.io -u "${GHCR_USER:-$(whoami)}" --password-stdin
fi

echo "Pulling ${ENTERPRISE_RAG_IMAGE}"
sudo docker pull "${ENTERPRISE_RAG_IMAGE}"
# Retag so compose services that share the image name stay consistent when
# compose file also references build context locally.
sudo docker tag "${ENTERPRISE_RAG_IMAGE}" enterprise-rag:latest || true

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" docker compose pull || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" docker compose up -d --remove-orphans --no-build
sudo docker image prune -f >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1/api/health >/dev/null; then
    echo
    sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" docker compose ps
    exit 0
  fi
  sleep 5
done

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" docker compose ps || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" docker compose logs --tail=80 api nginx || true
exit 1

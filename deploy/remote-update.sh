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
  exec env \
    ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-}" \
    ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE:-}" \
    AWS_REGION="${AWS_REGION:-us-east-1}" \
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
chmod 600 .env 2>/dev/null || true

# Default images if Actions did not pass them.
export ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-${ECR_REGISTRY}/enterprise-rag:main}"
export ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE:-${ECR_REGISTRY}/enterprise-rag-evals:main}"

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

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  docker compose pull || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  docker compose up -d --remove-orphans --no-build
sudo docker image prune -f >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1/api/health >/dev/null; then
    echo
    sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
      ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
      docker compose ps
    # Smoke: RAGAS import inside the evals image (Step 3 deps).
    if sudo docker exec enterprise-rag-evals python -c "from evals.metrics import run_all_metrics; print('ragas_ok')" 2>/dev/null; then
      echo "RAGAS import OK in enterprise-rag-evals"
    else
      echo "WARN: RAGAS import failed inside evals container" >&2
      sudo docker logs --tail=40 enterprise-rag-evals || true
      exit 1
    fi
    exit 0
  fi
  sleep 5
done

sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  docker compose ps || true
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  docker compose logs --tail=80 api evals nginx || true
exit 1

#!/usr/bin/env bash
# One-time Docker setup for a fresh Ubuntu t3.micro. Run on the instance:
#   REPO_URL=https://github.com/<you>/<repo>.git bash deploy/bootstrap-ec2.sh
#
# Prefers pulling pre-built ECR images (no heavy build on t3.micro).
# Instance role enterprise-rag-ec2 must allow ecr:GetAuthorizationToken + pull.
# After bootstrap (security group must allow TCP 80):
#   http://<public-ip>/           chat UI
#   http://<public-ip>/evals/     evals UI (RAGAS Step 3 in enterprise-rag-evals image)
#   http://<public-ip>/api/health FastAPI
set -euo pipefail

APP_DIR=/opt/enterprise-rag
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REGISTRY="${ECR_REGISTRY:-815398003681.dkr.ecr.us-east-1.amazonaws.com}"
REPO_URL="${REPO_URL:?Set REPO_URL to the GitHub clone URL}"
ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE:-${ECR_REGISTRY}/enterprise-rag:main}"
ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE:-${ECR_REGISTRY}/enterprise-rag-evals:main}"

sudo apt-get update
sudo apt-get install -y ca-certificates curl git unzip

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  sudo /tmp/aws/install
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  sudo mkdir -p "${APP_DIR}"
  sudo chown "$USER:$USER" "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Write the real values into ${APP_DIR}/.env before starting the stack."
fi
chmod 600 .env
mkdir -p processed_data

sudo systemctl disable --now enterprise-rag-api enterprise-rag-ui enterprise-rag-evals nginx 2>/dev/null || true

aws ecr get-login-password --region "${AWS_REGION}" \
  | sudo docker login --username AWS --password-stdin "${ECR_REGISTRY}"

export ENTERPRISE_RAG_IMAGE ENTERPRISE_RAG_EVALS_IMAGE
echo "Pulling ${ENTERPRISE_RAG_IMAGE}"
sudo docker pull "${ENTERPRISE_RAG_IMAGE}"
echo "Pulling ${ENTERPRISE_RAG_EVALS_IMAGE}"
sudo docker pull "${ENTERPRISE_RAG_EVALS_IMAGE}"
sudo ENTERPRISE_RAG_IMAGE="${ENTERPRISE_RAG_IMAGE}" \
  ENTERPRISE_RAG_EVALS_IMAGE="${ENTERPRISE_RAG_EVALS_IMAGE}" \
  docker compose up -d --remove-orphans --no-build

echo "Bootstrap done. Open security group port 80, then visit http://<public-ip>/"
echo "RAGAS Step 3 uses Portkey PORTKEY_FALLBACK_SLUG (Groq) + Jina embeddings."

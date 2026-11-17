#!/usr/bin/env bash
# One-time Docker setup for a fresh Ubuntu t3.micro. Run on the instance:
#   REPO_URL=https://github.com/<you>/<repo>.git bash deploy/bootstrap-ec2.sh
#
# After bootstrap (security group must allow TCP 80):
#   http://<public-ip>/           chat UI
#   http://<public-ip>/evals/     evals UI
#   http://<public-ip>/api/health FastAPI
set -euo pipefail

APP_DIR=/opt/enterprise-rag
REPO_URL="${REPO_URL:?Set REPO_URL to the GitHub clone URL}"

sudo apt-get update
sudo apt-get install -y ca-certificates curl git

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# Docker Engine + Compose plugin
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

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

# Stop legacy host systemd stack if present (ports / nginx conflict).
sudo systemctl disable --now enterprise-rag-api enterprise-rag-ui enterprise-rag-evals nginx 2>/dev/null || true

# First image build on t3.micro can take a long time — swap helps.
sudo docker compose build
sudo docker compose up -d --remove-orphans

echo "Bootstrap done. Open security group port 80, then visit http://<public-ip>/"
echo "If docker permission denied, log out/in (or use sudo docker) after usermod -aG docker."
